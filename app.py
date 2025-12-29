import streamlit as st
import yfinance as yf
import pandas as pd
import matplotlib.pyplot as plt
import google.generativeai as genai
from fpdf import FPDF
import os
from streamlit_oauth import OAuth2Component
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import time

# ---------------------------------------------------------
# 1. 앱 페이지 설정 및 Secrets 로드
# ---------------------------------------------------------
st.set_page_config(page_title="AI Stock DCA Master", layout="wide", page_icon="💰")

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
# 2. 구글 시트 DB 연결 및 관리 함수
# ---------------------------------------------------------
@st.cache_resource
def init_connection():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds_dict = dict(st.secrets["gcp_service_account"])
    # 줄바꿈 문자 처리 (에러 방지)
    if "private_key" in creds_dict:
        creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n").strip()
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    return gspread.authorize(creds)

def get_sheet(sheet_name):
    client = init_connection()
    try:
        return client.open("portfolio_db").worksheet(sheet_name)
    except:
        # 시트가 없으면 생성 시도
        sh = client.open("portfolio_db")
        ws = sh.add_worksheet(title=sheet_name, rows=100, cols=10)
        return ws

# --- 사용자 설정(프로필) 관련 함수 ---
def get_user_info(email):
    """이메일로 사용자 정보 조회"""
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
    """사용자 정보 저장"""
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

# --- 포트폴리오 DB 관련 함수 ---
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
# 3. 유틸리티 함수
# ---------------------------------------------------------
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

# --- [수정된 부분] Rate Limit 방지 데이터 로드 함수 ---
@st.cache_data(ttl=86400) # 24시간 캐싱
def load_data(ticker):
    """재시도 로직이 포함된 데이터 로드 함수"""
    max_retries = 3
    delay = 1
    for attempt in range(max_retries):
        try:
            data = yf.download(ticker, period="max", progress=False)
            if data is not None and not data.empty:
                if isinstance(data.columns, pd.MultiIndex):
                    data.columns = data.columns.get_level_values(0)
                return data
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
            else:
                st.error(f"데이터 다운로드 실패 ({ticker}): {e}")
    return None

def format_number(num):
    if num: return "{:,}".format(int(num))
    return "0"

def create_pdf(ticker, analysis_text, profit_rate, total_invested, final_value):
    pdf = FPDF()
    pdf.add_page()
    font_path = "NanumGothic.ttf" 
    if os.path.exists(font_path):
        pdf.add_font('Nanum', '', font_path, uni=True)
        pdf.set_font('Nanum', '', 12)
    else:
        pdf.set_font("Arial", size=12)
        pdf.cell(0, 10, txt="Error: Korean font not found.", ln=True)
    
    pdf.set_font_size(16)
    pdf.cell(0, 10, txt=f"[{ticker}] DCA Report", ln=True, align='C')
    pdf.ln(10)
    pdf.set_font_size(12)
    pdf.cell(0, 10, txt=f"Invested: {total_invested:,.0f}", ln=True)
    pdf.cell(0, 10, txt=f"Final: {final_value:,.0f} ({profit_rate:.2f}%)", ln=True)
    pdf.ln(10)
    pdf.multi_cell(0, 8, txt=analysis_text)
    return pdf.output(dest='S').encode('latin-1')

# ---------------------------------------------------------
# 4. 화면 구성
# ---------------------------------------------------------
def show_landing_page():
    # [복구됨] 풍성한 랜딩 페이지 소개 글
    st.markdown("""
    <div style='text-align: center; padding: 60px 0;'>
        <h1 style='color: #1E88E5; font-size: 3.5rem; font-weight: 700;'>🚀 AI Stock DCA Master</h1>
        <p style='font-size: 1.5rem; color: #555; margin-top: 10px;'>
            데이터 기반의 적립식 투자 검증부터 <br> 
            실전 포트폴리오 관리까지 한 번에 시작하세요.
        </p>
    </div>
    """, unsafe_allow_html=True)

    col1, col2, col3 = st.columns(3)
    with col1:
        st.info("📊 **과거 데이터 검증**\n\n매일, 매주, 매월 등 다양한 주기로 과거 수익률을 시뮬레이션하고 최적의 전략을 찾으세요.")
    with col2:
        st.success("🤖 **AI 투자 비서**\n\nGoogle Gemini가 분석한 전문적인 투자 리포트와 조언을 PDF로 받아보세요.")
    with col3:
        st.warning("💼 **실전 포트폴리오**\n\n실제 매매 내역을 구글 시트에 영구 저장하고, 실시간 수익률을 관리하세요.")

    st.divider()
    
    col_centered = st.columns([1, 2, 1])
    with col_centered[1]:
        st.markdown("<h3 style='text-align: center;'>지금 바로 시작하기</h3>", unsafe_allow_html=True)
        if CLIENT_ID and CLIENT_SECRET:
            oauth2 = OAuth2Component(CLIENT_ID, CLIENT_SECRET, AUTHORIZE_URL, TOKEN_URL, REVOKE_TOKEN_URL, REVOKE_TOKEN_URL)
            result = oauth2.authorize_button("Google 계정으로 계속하기", REDIRECT_URI, SCOPE, key="google_auth", use_container_width=True)
            if result:
                st.session_state["token"] = result.get("token")
                st.session_state["user_email"] = result.get("id_token", {}).get("email")
                st.rerun()

def show_main_app():
    user_email = st.session_state.get("user_email")
    if "user_info" not in st.session_state:
        st.session_state["user_info"] = get_user_info(user_email)
    
    user_info = st.session_state["user_info"]
    nickname = user_info.get("nickname", "투자자")

    # --- 사이드바 ---
    with st.sidebar:
        st.title(f"반가워요, {nickname}님! 👋")
        menu = st.radio("메뉴 이동", ["📊 시뮬레이션 & 포트폴리오", "⚙️ 회원 정보 수정"])
        st.divider()
        if st.button("로그아웃"):
            del st.session_state["token"]
            if "user_info" in st.session_state: del st.session_state["user_info"]
            st.rerun()

    # --- 회원 정보 수정 ---
    if menu == "⚙️ 회원 정보 수정":
        st.header("⚙️ 회원 정보 수정")
        st.write("여기서 설정한 **월 예산**은 시뮬레이션 시 기본값으로 사용됩니다.")
        with st.form("profile_form"):
            new_nick = st.text_input(
    "닉네임", 
    value=user_info.get("nickname", ""),
    autocomplete="nickname" # 'nickname' 속성 지정
)
            new_name = st.text_input(
    "이름", 
    value=user_info.get("name", ""),
    autocomplete="name", # 'name' 속성 지정
    placeholder="홍길동"
)
            current_budget = user_info.get("default_budget", 1000000)
           budget_str = st.text_input("매월 투자 예산 (원 또는 달러)", value=format_number(current_budget),
    autocomplete="transaction-amount", # 유효한 자동완성 값 제공
    help="브라우저 자동완성을 돕기 위해 예산 금액 성격을 지정했습니다.")
            
            if st.form_submit_button("저장하기"):
                try: clean_budget = int(budget_str.replace(",", ""))
                except: clean_budget = 0
                
                if update_user_info(user_email, new_nick, new_name, clean_budget):
                    st.session_state["user_info"] = {"nickname": new_nick, "name": new_name, "default_budget": clean_budget}
                    st.success("저장되었습니다!")
                    time.sleep(1)
                    st.rerun()

    # --- 메인 기능 ---
    elif menu == "📊 시뮬레이션 & 포트폴리오":
        st.title("💰 AI Stock DCA Master")
        
        with st.expander("🛠 **시뮬레이션 설정** (종목 및 예산)", expanded=True):
            c1, c2, c3 = st.columns(3)
            with c1: input_ticker = get_ticker(st.text_input("종목명 또는 코드", "삼성전자"))
            with c2:
                default_b = user_info.get("default_budget", 1000000)
                budget_input = st.text_input("매월 투자 예산 (원 또는 달러)", value=format_number(default_b))
                try: monthly_budget = int(budget_input.replace(",", ""))
                except: monthly_budget = 0
            with c3: interval_type = st.radio("매수 주기", ["매월", "매주", "매일"], horizontal=True)

            c4, c5 = st.columns([1, 2])
            with c4:
                target_day, target_date = None, None
                if interval_type == "매주": target_day = st.selectbox("요일", ["월요일", "화요일", "수요일", "목요일", "금요일"], index=4)
                elif interval_type == "매월": target_date = st.selectbox("날짜", [1, 15, 30], index=0)

        tab1, tab2 = st.tabs(["📈 DCA 백테스팅", "💼 내 포트폴리오"])

        with tab1:
            # [수정됨] 강화된 load_data 함수 사용
            raw_data = load_data(input_ticker)
            if raw_data is not None and not raw_data.empty:
                start_d = raw_data.index.min().date()
                end_d = raw_data.index.max().date()
                st.info(f"📅 데이터 기간: {start_d} ~ {end_d}")
                
                # [복구됨] 백테스팅 기간 입력 (슬라이더)
                years_avail = (end_d - start_d).days // 365
                test_period = st.slider("백테스팅 기간 (년)", 1, max(1, years_avail), min(3, max(1, years_avail)))
                
                if st.button("🚀 백테스팅 및 AI 분석 시작", type="primary"):
                    df = raw_data.last(f"{test_period}Y").copy()
                    
                    buy_indices = []
                    if interval_type == "매일": buy_indices = df.index
                    elif interval_type == "매주":
                        day_map = {"월요일":0,"화요일":1,"수요일":2,"목요일":3,"금요일":4}
                        buy_indices = df[df.index.weekday == day_map[target_day]].index
                    elif interval_type == "매월":
                        grouped = df.groupby([df.index.year, df.index.month])
                        for _, group in grouped:
                            candidates = group[group.index.day >= target_date]
                            buy_indices.append(candidates.index[0] if not candidates.empty else group.index[-1])

                    per_trade = monthly_budget
                    if interval_type == "매주": per_trade = monthly_budget * 12 / 52
                    elif interval_type == "매일": per_trade = monthly_budget * 12 / 250

                    st.write(f"💡 월 예산 **{format_number(monthly_budget)}원** 기준 ➡️ 1회 약 **{format_number(per_trade)}원** 투자")

                    total_invested, total_shares = 0, 0
                    balance_history = []
                    for date, row in df.iterrows():
                        if date in buy_indices:
                            qty = per_trade // row['Close']
                            if qty > 0:
                                total_invested += qty * row['Close']
                                total_shares += qty
                        balance_history.append(total_shares * row['Close'])

                    final_val = total_shares * df['Close'].iloc[-1]
                    profit_rate = (final_val - total_invested) / total_invested * 100 if total_invested > 0 else 0

                    c1, c2, c3 = st.columns(3)
                    c1.metric("총 투자금", f"{format_number(total_invested)}원")
                    c2.metric("최종 평가액", f"{format_number(final_val)}원")
                    c3.metric("수익률", f"{profit_rate:.2f}%")
                    st.line_chart(balance_history)
                    
                    with st.spinner("🤖 AI 분석 중..."):
                        if GEMINI_API_KEY:
                            model = genai.GenerativeModel('gemini-1.5-flash')
                            prompt = f"종목:{input_ticker},기간:{test_period}년,수익률:{profit_rate:.2f}%. 분석해줘."
                            try:
                                res = model.generate_content(prompt).text
                                st.success(res)
                                pdf_data = create_pdf(input_ticker, res, profit_rate, total_invested, final_val)
                                st.download_button("📄 PDF 다운로드", pdf_data, f"{input_ticker}_report.pdf", "application/pdf")
                            except: st.error("AI 분석 오류")
            else:
                st.error("데이터 로드 실패 (잠시 후 다시 시도해주세요)")

        with tab2:
            st.subheader("내 보유 자산 현황")
            df_port = get_portfolio_df(user_email)
            if not df_port.empty:
                summ = df_port.groupby('ticker').agg(
                    qty=('quantity','sum'), 
                    inv=('price', lambda x: (x * df_port.loc[x.index, 'quantity']).sum())
                ).reset_index()
                try:
                    cur_p = yf.download(summ['ticker'].tolist(), period='1d')['Close'].iloc[-1]
                    if len(summ) == 1: summ['cur'] = float(cur_p)
                    else: summ['cur'] = summ['ticker'].map(cur_p)
                except: summ['cur'] = 0
                
                summ['val'] = summ['cur'] * summ['qty']
                summ['rate'] = (summ['val'] - summ['inv']) / summ['inv'] * 100
                
                disp = summ.copy()
                disp['평단가'] = disp['inv'] / disp['qty']
                disp = disp[['ticker', 'qty', '평단가', '현재가', '수익률(%)']]
                disp.columns = ['종목', '보유수량', '평단가', '현재가', '수익률(%)']
                st.dataframe(disp.style.format({'평단가': "{:,.0f}", '현재가': "{:,.0f}", '수익률(%)': "{:.2f}%"}))
            else: st.info("아직 투자 기록이 없습니다.")

            st.divider()
            st.subheader("📝 매수 기록 추가")
            with st.form("trade_add"):
                c1, c2 = st.columns(2)
                t = c1.text_input("종목 코드", input_ticker)
                d = c2.date_input("날짜")
                c3, c4 = st.columns(2)
                p_str = c3.text_input("매수 단가 (원/달러)", value="0")
                q_str = c4.text_input("수량", value="1")
                if st.form_submit_button("기록 저장"):
                    try:
                        p = float(p_str.replace(",", ""))
                        q = int(q_str.replace(",", ""))
                        add_trade(user_email, t, d, p, q)
                        st.success("저장 완료!")
                        time.sleep(1)
                        st.rerun()
                    except: st.error("숫자 형식을 확인해주세요.")

# ---------------------------------------------------------
# 5. 실행 제어
# ---------------------------------------------------------
if "token" not in st.session_state:
    show_landing_page()
else:
    show_main_app()
