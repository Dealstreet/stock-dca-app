import streamlit as st
import yfinance as yf
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.font_manager as fm
import google.generativeai as genai
from fpdf import FPDF
import os
from streamlit_oauth import OAuth2Component
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import time
import urllib.request
from scipy import optimize
from io import BytesIO
import numpy as np

# ---------------------------------------------------------
# 1. 앱 설정 및 Secrets 로드
# ---------------------------------------------------------
st.set_page_config(page_title="AI Stock DCA Master Pro", layout="wide", page_icon="📈")

GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY")
CLIENT_ID = st.secrets.get("GOOGLE_CLIENT_ID")
CLIENT_SECRET = st.secrets.get("GOOGLE_CLIENT_SECRET")
REDIRECT_URI = st.secrets.get("REDIRECT_URI")

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_TOKEN_URL = "https://oauth2.googleapis.com/revoke"
SCOPE = "openid email profile"

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# ---------------------------------------------------------
# 2. 구글 시트 DB 연결 및 사용자 관리
# ---------------------------------------------------------
@st.cache_resource
def init_connection():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds_dict = dict(st.secrets["gcp_service_account"])
    if "private_key" in creds_dict:
        creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n").strip()
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    return gspread.authorize(creds)

def get_sheet(sheet_name):
    client = init_connection()
    try:
        return client.open("portfolio_db").worksheet(sheet_name)
    except:
        sh = client.open("portfolio_db")
        ws = sh.add_worksheet(title=sheet_name, rows=100, cols=10)
        return ws

def get_user_info(email):
    try:
        sheet = get_sheet("user_settings")
        records = sheet.get_all_records()
        df = pd.DataFrame(records)
        if not df.empty and email in df['email'].values:
            user_data = df[df['email'] == email].iloc[0]
            return {
                "nickname": user_data['nickname'],
                "name": user_data['name'],
                "default_budget": int(str(user_data['default_budget']).replace(',', ''))
            }
    except Exception:
        pass
    return {"nickname": "투자자", "name": "", "default_budget": 1000000}

def update_user_info(email, nickname, name, budget):
    try:
        sheet = get_sheet("user_settings")
        records = sheet.get_all_records()
        df = pd.DataFrame(records)
        if not df.empty and email in df['email'].values:
            cell = sheet.find(email)
            sheet.update_cell(cell.row, 2, nickname)
            sheet.update_cell(cell.row, 3, name)
            sheet.update_cell(cell.row, 4, budget)
        else:
            if not records: sheet.append_row(["email", "nickname", "name", "default_budget"])
            sheet.append_row([email, nickname, name, budget])
        return True
    except Exception as e:
        st.error(f"저장 실패: {e}")
        return False

def add_trade(user_email, ticker, date, price, quantity):
    try:
        sheet = get_sheet("sheet1")
        if not sheet.get_all_values():
            sheet.append_row(["user_email", "ticker", "date", "price", "quantity"])
        sheet.append_row([user_email, ticker, str(date), price, int(quantity)])
    except Exception as e:
        st.error(f"매수 기록 저장 실패: {e}")

def get_portfolio_df(user_email):
    try:
        sheet = get_sheet("sheet1")
        data = sheet.get_all_records()
        df = pd.DataFrame(data)
        if not df.empty:
            df['price'] = pd.to_numeric(df['price'], errors='coerce')
            df['quantity'] = pd.to_numeric(df['quantity'], errors='coerce')
            return df[df['user_email'] == user_email]
        return pd.DataFrame()
    except: return pd.DataFrame()

# ---------------------------------------------------------
# 3. 고급 분석 및 시각화 헬퍼 함수
# ---------------------------------------------------------

# 폰트 설정
def set_korean_font():
    font_path = "NanumGothic-Regular.ttf"
    if not os.path.exists(font_path):
        urllib.request.urlretrieve("https://github.com/Dealstreet/stock-dca-app/raw/refs/heads/main/NanumGothic-Regular.ttf", font_path)
    font_prop = fm.FontProperties(fname=font_path)
    plt.rcParams['font.family'] = font_prop.get_name()
    plt.rcParams['axes.unicode_minus'] = False
    return font_prop

# 환율 정보
@st.cache_data(ttl=3600)
def get_exchange_rate():
    try:
        df = yf.download("KRW=X", period="1d", progress=False)
        if not df.empty:
            return float(df['Close'].iloc[-1])
    except: pass
    return 1400.0

# 종목 검색
def get_ticker(query):
    query = query.strip()
    mapping = {
        "삼성전자": "005930.KS", "SK하이닉스": "000660.KS", "현대차": "005380.KS",
        "애플": "AAPL", "테슬라": "TSLA", "엔비디아": "NVDA", "마이크로소프트": "MSFT",
        "비트코인": "BTC-USD", "나스닥100": "QQQ", "S&P500": "SPY", "슈드": "SCHD"
    }
    if query in mapping: return mapping[query]
    if query.isdigit() and len(query) == 6: return f"{query}.KS"
    return query

# 데이터 로드 (배당 포함)
@st.cache_data(ttl=3600)
def load_data(ticker):
    try:
        ticker_obj = yf.Ticker(ticker)
        data = ticker_obj.history(period="max")
        if not data.empty:
            data.index = data.index.tz_localize(None)
            return data
    except Exception as e:
        st.error(f"데이터 다운로드 실패: {e}")
    return None

# XIRR 계산
def xirr(cashflows, dates):
    if len(cashflows) != len(dates): return None
    def npv(rate):
        if rate <= -1.0: return float('inf')
        d0 = dates[0]
        return sum([cf / ((1 + rate) ** ((d - d0).days / 365.0)) for cf, d in zip(cashflows, dates)])
    try:
        return optimize.newton(npv, 0.1)
    except: return None

# 화폐 단위 포맷팅
def format_currency(value, unit="원"):
    if unit == "만원": return f"{value/10000:,.0f}만원"
    elif unit == "백만원": return f"{value/1000000:,.2f}백만원"
    elif unit == "억원": return f"{value/100000000:,.4f}억원"
    else: return f"{value:,.0f}원"

def format_number(num):
    if num: return "{:,}".format(int(num))
    return "0"

# Matplotlib 차트 생성
def create_chart(df_history, ticker_name):
    font_prop = set_korean_font()
    fig, ax = plt.subplots(figsize=(10, 6))
    dates = df_history['date']
    
    # 3가지 선 그리기
    ax.plot(dates, df_history['total_value'], label='포트폴리오 가치', color='#FF5733', linewidth=2, marker='o', markevery=10, markersize=5)
    ax.plot(dates, df_history['invested'], label='총 투자원금', color='#333333', linestyle='--', linewidth=1.5)
    ax.plot(dates, df_history['inflation_principal'], label='물가상승원금선 (연2%)', color='#2E86C1', linestyle=':', linewidth=1.5)
    
    ax.set_title(f"[{ticker_name}] DCA 투자 성과 추이", fontproperties=font_prop, fontsize=16)
    ax.set_xlabel("기간 (월)", fontproperties=font_prop)
    ax.set_ylabel("평가 금액", fontproperties=font_prop)
    
    # X축 설정
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=max(1, len(dates)//10)))
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    plt.xticks(rotation=45)
    
    ax.legend(prop=font_prop)
    ax.grid(True, linestyle='--', alpha=0.5)
    plt.tight_layout()
    
    buf = BytesIO()
    plt.savefig(buf, format="png", dpi=100)
    buf.seek(0)
    plt.close(fig)
    return buf

# PDF 생성
def create_pdf(ticker, analysis_text, profit_rate, xirr_val, total_invested, final_value, excess_return, chart_buf):
    font_urls = {
        "NanumGothic-Regular.ttf": "https://github.com/Dealstreet/stock-dca-app/raw/refs/heads/main/NanumGothic-Regular.ttf",
        "NanumGothic-Bold.ttf": "https://github.com/Dealstreet/stock-dca-app/raw/refs/heads/main/NanumGothic-Bold.ttf"
    }
    for filename, url in font_urls.items():
        if not os.path.exists(filename):
            try: urllib.request.urlretrieve(url, filename)
            except: pass

    pdf = FPDF()
    pdf.add_page()
    
    has_korean = os.path.exists("NanumGothic-Regular.ttf")
    if has_korean:
        pdf.add_font('Nanum', '', 'NanumGothic-Regular.ttf', uni=True)
        pdf.add_font('Nanum', 'B', 'NanumGothic-Bold.ttf', uni=True)
        pdf.set_font('Nanum', 'B', 20)
    else: pdf.set_font('Arial', 'B', 20)
        
    pdf.cell(0, 15, txt=f"[{ticker}] Investment Report", ln=True, align='C')
    pdf.ln(5)
    
    if has_korean: pdf.set_font('Nanum', '', 12)
    else: pdf.set_font('Arial', '', 12)
    
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(0, 10, txt=f" Total Invested: {total_invested:,.0f} KRW", ln=True, fill=True)
    pdf.cell(0, 10, txt=f" Final Value: {final_value:,.0f} KRW", ln=True, fill=True)
    pdf.cell(0, 10, txt=f" Return: {profit_rate:.2f}% | XIRR: {xirr_val:.2f}%", ln=True, fill=True)
    pdf.cell(0, 10, txt=f" Excess Return (vs 2% Inf): {excess_return:,.0f} KRW", ln=True, fill=True)
    pdf.ln(10)
    
    if chart_buf:
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmpfile:
            tmpfile.write(chart_buf.getvalue())
            tmp_path = tmpfile.name
        pdf.image(tmp_path, x=10, w=190)
        os.unlink(tmp_path)
    pdf.ln(10)
    
    pdf.multi_cell(0, 8, txt=analysis_text)
    return pdf.output(dest='S').encode('latin-1')

# ---------------------------------------------------------
# 4. 화면 구성 (Landing & Main)
# ---------------------------------------------------------
def show_landing_page():
    st.markdown("""
    <div style='text-align: center; padding: 60px 0;'>
        <h1 style='color: #1E88E5; font-size: 3.5rem; font-weight: 700;'>🚀 AI Stock DCA Master Pro</h1>
        <p style='font-size: 1.5rem; color: #555; margin-top: 10px;'>
            데이터 기반의 적립식 투자 검증부터 <br> 
            실전 포트폴리오 관리까지 한 번에 시작하세요.
        </p>
    </div>
    """, unsafe_allow_html=True)
    col1, col2, col3 = st.columns(3)
    with col1: st.info("📊 **과거 데이터 검증 (XIRR)**")
    with col2: st.success("🤖 **AI 투자 비서 & PDF**")
    with col3: st.warning("💼 **실전 포트폴리오 관리**")
    st.divider()
    col_centered = st.columns([1, 2, 1])
    with col_centered[1]:
        if CLIENT_ID and CLIENT_SECRET:
            oauth2 = OAuth2Component(CLIENT_ID, CLIENT_SECRET, AUTHORIZE_URL, TOKEN_URL, REVOKE_TOKEN_URL, REVOKE_TOKEN_URL)
            result = oauth2.authorize_button("Google 계정으로 계속하기", REDIRECT_URI, SCOPE, key="google_auth", use_container_width=True)
            if result:
                st.session_state["token"] = result.get("token")
                st.session_state["user_email"] = result.get("id_token", {}).get("email")
                st.rerun()
        else:
            st.error("Google Client ID/Secret 설정이 필요합니다.")

def show_main_app():
    user_email = st.session_state.get("user_email")
    if "user_info" not in st.session_state:
        st.session_state["user_info"] = get_user_info(user_email)
    
    user_info = st.session_state["user_info"]
    nickname = user_info.get("nickname", "투자자")

    with st.sidebar:
        st.title(f"반가워요, {nickname}님! 👋")
        menu = st.radio("메뉴 이동", ["📊 시뮬레이션 & 포트폴리오", "⚙️ 회원 정보 수정"])
        st.divider()
        if st.button("로그아웃"):
            del st.session_state["token"]
            if "user_info" in st.session_state: del st.session_state["user_info"]
            st.rerun()

    if menu == "⚙️ 회원 정보 수정":
        st.header("⚙️ 회원 정보 수정")
        with st.form("profile_form"):
            new_nick = st.text_input("닉네임", value=user_info.get("nickname", ""), autocomplete="nickname")
            new_name = st.text_input("이름", value=user_info.get("name", ""), autocomplete="name")
            current_budget = user_info.get("default_budget", 1000000)
            budget_str = st.text_input("매월 투자 예산", value=format_number(current_budget), autocomplete="transaction-amount")
            
            if st.form_submit_button("저장하기"):
                try: clean_budget = int(budget_str.replace(",", ""))
                except: clean_budget = 0
                if update_user_info(user_email, new_nick, new_name, clean_budget):
                    st.session_state["user_info"] = {"nickname": new_nick, "name": new_name, "default_budget": clean_budget}
                    st.success("저장되었습니다!")
                    time.sleep(1)
                    st.rerun()

    elif menu == "📊 시뮬레이션 & 포트폴리오":
        st.title("💰 AI Stock DCA Master Pro")
        
        tab1, tab2 = st.tabs(["📈 DCA 백테스팅", "💼 내 포트폴리오"])

        with tab1:
            with st.expander("🛠 **시뮬레이션 고급 설정**", expanded=True):
                c1, c2, c3 = st.columns(3)
                with c1: 
                    input_query = st.text_input("종목명 또는 코드", "삼성전자")
                    input_ticker = get_ticker(input_query)
                with c2:
                    default_b = user_info.get("default_budget", 1000000)
                    budget_str = st.text_input("매월 투자 예산", value=format_number(default_b))
                    try: monthly_budget = int(budget_str.replace(",", "").replace("원", ""))
                    except: monthly_budget = 0
                with c3:
                    interval_type = st.selectbox("매수 주기", ["매월", "매주", "매일"])

                c4, c5, c6 = st.columns(3)
                with c4: years = st.slider("기간 (년)", 1, 10, 3)
                with c5: use_dividend = st.checkbox("배당금 재투자 (TR 효과)", value=True)
                with c6: ai_use = st.checkbox("AI 투자 분석 리포트 생성", value=False)
                
                usd_krw = get_exchange_rate()
                st.caption(f"ℹ️ 환율 적용: 1 USD = {usd_krw:,.2f} KRW (해외 주식 시)")

            if st.button("🚀 시뮬레이션 시작", type="primary"):
                raw_data = load_data(input_ticker)
                if raw_data is not None and not raw_data.empty:
                    # 데이터 처리 시작
                    currency_symbol = "₩"
                    is_us_stock = False
                    if "Close" in raw_data.columns:
                        if not (input_ticker.endswith(".KS") or input_ticker.endswith(".KQ")):
                            is_us_stock = True; currency_symbol = "$"
                    
                    end_date = raw_data.index.max()
                    start_date = end_date - pd.DateOffset(years=years)
                    df = raw_data[raw_data.index >= start_date].copy()
                    
                    per_trade_krw = monthly_budget
                    if interval_type == "매주": per_trade_krw = monthly_budget * 12 / 52
                    elif interval_type == "매일": per_trade_krw = monthly_budget * 12 / 250
                    
                    per_trade_amt = per_trade_krw / usd_krw if is_us_stock else per_trade_krw
                    
                    total_shares = 0
                    total_invested_currency = 0
                    inflation_principal = 0
                    
                    history = []
                    cashflows = [] # XIRR용
                    
                    buy_indices = []
                    if interval_type == "매일": buy_indices = df.index
                    elif interval_type == "매월": buy_indices = df.groupby([df.index.year, df.index.month]).apply(lambda x: x.index[0]).tolist()
                    elif interval_type == "매주": buy_indices = df[df.index.dayofweek == 4].index

                    prev_date = df.index[0]
                    
                    for date, row in df.iterrows():
                        price = row['Close']
                        days_diff = (date - prev_date).days
                        if inflation_principal > 0: inflation_principal *= (1.02) ** (days_diff / 365)
                        prev_date = date

                        if use_dividend and row.get('Dividends', 0) > 0:
                            total_shares += (row['Dividends'] * total_shares) / price
                        
                        if date in buy_indices:
                            total_shares += per_trade_amt / price
                            total_invested_currency += per_trade_amt
                            inflation_principal += per_trade_amt * (usd_krw if is_us_stock else 1)
                            invest_krw = per_trade_amt * (usd_krw if is_us_stock else 1)
                            cashflows.append(-invest_krw)
                        
                        rate = usd_krw if is_us_stock else 1
                        history.append({
                            "date": date,
                            "invested": total_invested_currency * rate,
                            "total_value": total_shares * price * rate,
                            "inflation_principal": inflation_principal
                        })
                    
                    df_res = pd.DataFrame(history)
                    final_invested_krw = df_res['invested'].iloc[-1]
                    final_value_krw = df_res['total_value'].iloc[-1]
                    final_inf_krw = df_res['inflation_principal'].iloc[-1]
                    
                    profit_rate = (final_value_krw - final_invested_krw) / final_invested_krw * 100
                    excess_return = final_value_krw - final_inf_krw
                    
                    # XIRR
                    xirr_dates = [d for d in buy_indices if d <= end_date] + [df_res['date'].iloc[-1]]
                    xirr_flows = [-per_trade_krw] * len([d for d in buy_indices if d <= end_date]) + [final_value_krw]
                    try: xirr_val = xirr(xirr_flows, xirr_dates) * 100
                    except: xirr_val = 0.0

                    st.divider()
                    st.subheader(f"📊 {input_ticker} ({input_query}) 분석 결과")
                    unit_opt = st.radio("금액 단위 선택", ["원", "만원", "백만원", "억원"], horizontal=True)
                    
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("총 투자원금", format_currency(final_invested_krw, unit_opt))
                    c2.metric("최종 평가액", format_currency(final_value_krw, unit_opt))
                    c3.metric("수익률 / XIRR", f"{profit_rate:.1f}% / {xirr_val:.1f}%")
                    c4.metric("초과 수익 (vs 물가2%)", format_currency(excess_return, unit_opt), delta_color="normal" if excess_return > 0 else "inverse")
                    
                    chart_buf = create_chart(df_res, input_query)
                    st.image(chart_buf, use_container_width=True)
                    
                    ai_text = "AI 분석 미사용"
                    if ai_use and GEMINI_API_KEY:
                        with st.spinner("🤖 AI 분석 중..."):
                            prompt = f"""
                            당신은 펀드매니저입니다. {input_query} 투자 분석:
                            기간: {years}년, 투자금: {monthly_budget}원/월
                            결과: 원금 {final_invested_krw:,.0f}원 -> {final_value_krw:,.0f}원
                            수익률: {profit_rate:.2f}% (XIRR: {xirr_val:.2f}%)
                            초과수익: {excess_return:,.0f}원
                            DCA 전략 평가와 조언을 300자 내외로 작성.
                            """
                            try: ai_text = genai.GenerativeModel("gemini-pro").generate_content(prompt).text
                            except: ai_text = "AI 호출 실패"
                            st.info(ai_text)
                    
                    pdf_data = create_pdf(input_query, ai_text, profit_rate, xirr_val, final_invested_krw, final_value_krw, excess_return, chart_buf)
                    st.download_button("📄 PDF 리포트 다운로드", pdf_data, f"{input_query}_report.pdf", "application/pdf")
                else: st.error("데이터 로드 실패")

        with tab2:
            st.subheader("내 보유 자산 현황")
            df_port = get_portfolio_df(user_email)
            if not df_port.empty:
                summ = df_port.groupby('ticker').agg(qty=('quantity','sum'), inv=('price', lambda x: (x * df_port.loc[x.index, 'quantity']).sum())).reset_index()
                
                # 현재가 조회 최적화
                tickers = summ['ticker'].tolist()
                try:
                    cur_data = yf.download(tickers, period='1d', group_by='ticker', progress=False)
                    prices = {}
                    if len(tickers) == 1:
                        if isinstance(cur_data.columns, pd.MultiIndex): prices[tickers[0]] = float(cur_data.iloc[-1][(tickers[0], 'Close')])
                        else: prices[tickers[0]] = float(cur_data.iloc[-1]['Close'])
                    else:
                        for t in tickers:
                            try: prices[t] = float(cur_data.iloc[-1][(t, 'Close')])
                            except: prices[t] = 0
                    summ['cur'] = summ['ticker'].map(prices)
                except: summ['cur'] = 0
                
                summ['val'] = summ['cur'] * summ['qty']
                summ['rate'] = (summ['val'] - summ['inv']) / summ['inv'] * 100
                
                disp = summ[['ticker', 'qty', 'inv', 'cur', 'rate']].copy()
                disp.columns = ['종목', '보유수량', '총매수금액', '현재평가액', '수익률(%)']
                st.dataframe(disp.style.format({'총매수금액': "{:,.0f}", '현재평가액': "{:,.0f}", '수익률(%)': "{:.2f}%"}))
            else: st.info("투자 기록이 없습니다.")
            
            st.divider()
            st.subheader("📝 매수 기록 추가")
            with st.form("trade_add"):
                c1, c2 = st.columns(2)
                t = c1.text_input("종목 코드")
                d = c2.date_input("날짜")
                c3, c4 = st.columns(2)
                p = c3.text_input("매수 단가 (원/달러)", "0")
                q = c4.text_input("수량", "1")
                if st.form_submit_button("저장"):
                    try: add_trade(user_email, t, d, float(p.replace(",","")), int(q.replace(",","")))
                    except: st.error("입력 오류")
                    st.rerun()

# ---------------------------------------------------------
# 5. 실행
# ---------------------------------------------------------
if __name__ == "__main__":
    if "token" not in st.session_state:
        show_landing_page()
    else:
        show_main_app()
