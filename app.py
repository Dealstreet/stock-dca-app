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
# 1. 앱 설정
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
# 2. 헬퍼 함수
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

# MDD 계산 함수
def calculate_mdd(prices):
    roll_max = prices.cummax()
    drawdown = prices / roll_max - 1.0
    mdd = drawdown.min()
    return mdd * 100

# 차트 생성 (수정됨: 26회차 마커, 텍스트 표시)
def create_chart(df_history, ticker_name, unit_divider=1, unit_label="원"):
    font_prop = set_korean_font()
    fig, ax = plt.subplots(figsize=(12, 7)) # 차트 크기 약간 키움
    
    dates = df_history['date']
    # 단위 변환 적용
    val_series = df_history['total_value'] / unit_divider
    inv_series = df_history['invested'] / unit_divider
    inf_series = df_history['inflation_principal'] / unit_divider
    
    # 1. 메인 라인 그리기
    ax.plot(dates, val_series, label='포트폴리오 가치', color='#FF5733', linewidth=2)
    ax.plot(dates, inv_series, label='총 투자원금', color='#333333', linestyle='--', linewidth=1.5)
    ax.plot(dates, inf_series, label='물가상승원금선 (연2%)', color='#2E86C1', linestyle=':', linewidth=1.5)
    
    # 2. 26회차마다 마커 및 텍스트 표시
    # 데이터가 너무 적을 경우를 대비해 최소 간격 조정
    interval = 26
    
    for i in range(0, len(dates), interval):
        date_val = dates.iloc[i]
        price_val = val_series.iloc[i]
        
        # 마커 찍기
        ax.plot(date_val, price_val, marker='o', color='#C70039', markersize=6)
        
        # 텍스트 (회차 및 금액)
        # 겹침 방지를 위해 텍스트 위치 약간 위로 조정
        label_text = f"{i+1}회\n{price_val:,.0f}{unit_label}"
        ax.annotate(label_text, 
                    xy=(date_val, price_val), 
                    xytext=(0, 10), textcoords='offset points',
                    ha='center', fontsize=8, fontproperties=font_prop,
                    bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="gray", alpha=0.7))

    ax.set_title(f"[{ticker_name}] DCA 투자 성과 추이", fontproperties=font_prop, fontsize=16)
    ax.set_xlabel("기간 (월)", fontproperties=font_prop)
    ax.set_ylabel(f"평가 금액 ({unit_label})", fontproperties=font_prop)
    
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

# 기타 필수 함수들 (기존 유지)
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
    try: return client.open("portfolio_db").worksheet(sheet_name)
    except: return client.open("portfolio_db").add_worksheet(title=sheet_name, rows=100, cols=10)

def get_user_info(email):
    try:
        sheet = get_sheet("user_settings")
        df = pd.DataFrame(sheet.get_all_records())
        if not df.empty and email in df['email'].values:
            u = df[df['email'] == email].iloc[0]
            return {"nickname": u['nickname'], "name": u['name'], "default_budget": int(str(u['default_budget']).replace(',', ''))}
    except: pass
    return {"nickname": "투자자", "name": "", "default_budget": 1000000}

def update_user_info(email, nick, name, bud):
    try:
        sheet = get_sheet("user_settings")
        df = pd.DataFrame(sheet.get_all_records())
        if not df.empty and email in df['email'].values:
            r = sheet.find(email).row
            sheet.update_cell(r, 2, nick); sheet.update_cell(r, 3, name); sheet.update_cell(r, 4, bud)
        else:
            if not sheet.get_all_values(): sheet.append_row(["email", "nickname", "name", "default_budget"])
            sheet.append_row([email, nick, name, bud])
        return True
    except: return False

def add_trade(email, t, d, p, q):
    try:
        s = get_sheet("sheet1")
        if not s.get_all_values(): s.append_row(["user_email", "ticker", "date", "price", "quantity"])
        s.append_row([email, t, str(d), p, int(q)])
    except: pass

def get_portfolio_df(email):
    try:
        s = get_sheet("sheet1")
        df = pd.DataFrame(s.get_all_records())
        if not df.empty:
            df['price'] = pd.to_numeric(df['price'], errors='coerce')
            df['quantity'] = pd.to_numeric(df['quantity'], errors='coerce')
            return df[df['user_email'] == email]
    except: pass
    return pd.DataFrame()

@st.cache_data(ttl=3600)
def get_exchange_rate():
    try:
        df = yf.download("KRW=X", period="1d", progress=False)
        return float(df['Close'].iloc[-1])
    except: return 1400.0

def get_ticker(q):
    q = q.strip()
    m = {"삼성전자": "005930.KS", "SK하이닉스": "000660.KS", "현대차": "005380.KS", "애플": "AAPL", "테슬라": "TSLA", "엔비디아": "NVDA", "마이크로소프트": "MSFT", "비트코인": "BTC-USD", "나스닥100": "QQQ", "S&P500": "SPY", "슈드": "SCHD"}
    return m.get(q, f"{q}.KS" if q.isdigit() and len(q)==6 else q)

@st.cache_data(ttl=3600)
def load_data(t):
    try:
        d = yf.Ticker(t).history(period="max")
        if not d.empty: d.index = d.index.tz_localize(None); return d
    except: pass
    return None

def xirr(cf, d):
    if len(cf) != len(d): return None
    def npv(r):
        if r <= -1.0: return float('inf')
        d0 = d[0]; return sum([c / ((1 + r) ** ((dt - d0).days / 365.0)) for c, dt in zip(cf, d)])
    try: return optimize.newton(npv, 0.1)
    except: return None

def format_currency(v, u="원"):
    if u == "만원": return f"{v/10000:,.0f}만원"
    elif u == "백만원": return f"{v/1000000:,.2f}백만원"
    elif u == "억원": return f"{v/100000000:,.4f}억원"
    return f"{v:,.0f}원"

def format_number(n): return "{:,}".format(int(n)) if n else "0"

def create_pdf(ticker, ai_txt, prof, xirr_v, inv, val, exc, chart_buf, mdd):
    font_urls = {"NanumGothic-Regular.ttf": "https://github.com/Dealstreet/stock-dca-app/raw/refs/heads/main/NanumGothic-Regular.ttf", "NanumGothic-Bold.ttf": "https://github.com/Dealstreet/stock-dca-app/raw/refs/heads/main/NanumGothic-Bold.ttf"}
    for f, u in font_urls.items():
        if not os.path.exists(f): 
            try: urllib.request.urlretrieve(u, f)
            except: pass
            
    pdf = FPDF()
    pdf.add_page()
    hk = os.path.exists("NanumGothic-Regular.ttf")
    pdf.add_font('Nanum', '', 'NanumGothic-Regular.ttf', uni=True) if hk else None
    pdf.add_font('Nanum', 'B', 'NanumGothic-Bold.ttf', uni=True) if hk else None
    pdf.set_font('Nanum' if hk else 'Arial', 'B', 20)
    
    pdf.cell(0, 15, txt=f"[{ticker}] Investment Report", ln=True, align='C')
    pdf.ln(5)
    
    pdf.set_font('Nanum' if hk else 'Arial', '', 12)
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(0, 10, txt=f" Total Invested: {inv:,.0f} KRW", ln=True, fill=True)
    pdf.cell(0, 10, txt=f" Final Value: {val:,.0f} KRW", ln=True, fill=True)
    pdf.cell(0, 10, txt=f" Return: {prof:.2f}% | XIRR: {xirr_v:.2f}% | MDD: {mdd:.2f}%", ln=True, fill=True)
    pdf.cell(0, 10, txt=f" Excess Return: {exc:,.0f} KRW", ln=True, fill=True)
    pdf.ln(10)
    
    if chart_buf:
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
            tmp.write(chart_buf.getvalue()); tmp_path = tmp.name
        pdf.image(tmp_path, x=10, w=190); os.unlink(tmp_path)
    pdf.ln(10)
    pdf.multi_cell(0, 8, txt=ai_txt)
    return pdf.output(dest='S').encode('latin-1')

# ---------------------------------------------------------
# 3. 메인 로직
# ---------------------------------------------------------
def show_landing_page():
    st.markdown("<h1 style='text-align: center;'>🚀 AI Stock DCA Master Pro</h1>", unsafe_allow_html=True)
    if CLIENT_ID and CLIENT_SECRET:
        oauth2 = OAuth2Component(CLIENT_ID, CLIENT_SECRET, AUTHORIZE_URL, TOKEN_URL, REVOKE_TOKEN_URL, REVOKE_TOKEN_URL)
        result = oauth2.authorize_button("Google 로그인", REDIRECT_URI, SCOPE, key="google_auth", use_container_width=True)
        if result:
            st.session_state["token"] = result.get("token")
            st.session_state["user_email"] = result.get("id_token", {}).get("email")
            st.rerun()

def show_main_app():
    user_email = st.session_state.get("user_email")
    if "user_info" not in st.session_state: st.session_state["user_info"] = get_user_info(user_email)
    user_info = st.session_state["user_info"]
    
    with st.sidebar:
        st.title(f"{user_info.get('nickname')}님 환영합니다")
        menu = st.radio("메뉴", ["📊 시뮬레이션", "⚙️ 정보 수정"])
        if st.button("로그아웃"):
            del st.session_state["token"]; del st.session_state["user_info"]; st.rerun()

    if menu == "⚙️ 정보 수정":
        st.header("정보 수정")
        with st.form("pf"):
            nn = st.text_input("닉네임", user_info.get("nickname"))
            nm = st.text_input("이름", user_info.get("name"))
            b = st.text_input("예산", format_number(user_info.get("default_budget")))
            if st.form_submit_button("저장"):
                try: cb = int(b.replace(",",""))
                except: cb = 0
                if update_user_info(user_email, nn, nm, cb):
                    st.session_state["user_info"] = {"nickname": nn, "name": nm, "default_budget": cb}
                    st.success("저장됨"); time.sleep(1); st.rerun()

    elif menu == "📊 시뮬레이션":
        st.title("💰 DCA 시뮬레이터")
        tab1, tab2 = st.tabs(["시뮬레이션", "내 포트폴리오"])
        
        with tab1:
            with st.expander("설정", expanded=True):
                c1, c2, c3 = st.columns(3)
                iq = c1.text_input("종목", "삼성전자"); it = get_ticker(iq)
                bs = c2.text_input("예산", format_number(user_info.get("default_budget")))
                try: mb = int(bs.replace(",",""))
                except: mb = 0
                intv = c3.selectbox("주기", ["매월", "매주", "매일"])
                
                # [복구] 상세 날짜/요일 선택
                target_day, target_date = "금요일", 1
                c4, c5 = st.columns([1, 2])
                with c4:
                    if intv == "매주":
                        target_day = st.selectbox("요일 선택", ["월요일", "화요일", "수요일", "목요일", "금요일"], index=4)
                    elif intv == "매월":
                        target_date = st.selectbox("매수 날짜", [1, 15, 30], index=0)

                c6, c7, c8 = st.columns(3)
                yrs = c6.slider("기간(년)", 1, 10, 3)
                div = c7.checkbox("배당재투자", True)
                ai = c8.checkbox("AI 분석", False)
                uk = get_exchange_rate()
                st.caption(f"환율: 1$ = {uk:,.2f}원")

            # 시뮬레이션 실행 및 데이터 저장 (Session State 사용)
            if st.button("🚀 시뮬레이션 시작", type="primary"):
                raw = load_data(it)
                if raw is not None:
                    # 데이터 처리
                    is_us = False; sym = "₩"
                    if "Close" in raw.columns:
                        if not (it.endswith(".KS") or it.endswith(".KQ")): is_us = True; sym = "$"
                    
                    df = raw[raw.index >= (raw.index.max() - pd.DateOffset(years=yrs))].copy()
                    
                    # 주기별 매수일 설정 [복구됨]
                    bi = []
                    if intv == "매일": bi = df.index
                    elif intv == "매월":
                        # 해당 날짜 혹은 그 이후 가장 가까운 날 찾기
                        grouped = df.groupby([df.index.year, df.index.month])
                        for _, g in grouped:
                            candidates = g[g.index.day >= target_date]
                            if not candidates.empty: bi.append(candidates.index[0])
                            else: bi.append(g.index[-1])
                    elif intv == "매주":
                        d_map = {"월요일":0, "화요일":1, "수요일":2, "목요일":3, "금요일":4}
                        bi = df[df.index.dayofweek == d_map[target_day]].index

                    # 계산 로직
                    pt_krw = mb
                    if intv == "매주": pt_krw = mb * 12 / 52
                    elif intv == "매일": pt_krw = mb * 12 / 250
                    
                    pt_amt = pt_krw / uk if is_us else pt_krw
                    shares = 0; inv_curr = 0; inf_p = 0
                    hist = []; xirr_fs = []; prev = df.index[0]
                    
                    for d, r in df.iterrows():
                        p = r['Close']
                        days = (d - prev).days
                        if inf_p > 0: inf_p *= (1.02) ** (days/365)
                        prev = d
                        
                        if div and r.get('Dividends', 0) > 0: shares += (r['Dividends']*shares)/p
                        
                        if d in bi:
                            shares += pt_amt/p
                            inv_curr += pt_amt
                            inf_p += pt_amt * (uk if is_us else 1)
                            xirr_fs.append(-pt_krw)
                        
                        rate = uk if is_us else 1
                        hist.append({"date": d, "invested": inv_curr*rate, "total_value": shares*p*rate, "inflation_principal": inf_p})
                    
                    res_df = pd.DataFrame(hist)
                    fin_inv = res_df['invested'].iloc[-1]
                    fin_val = res_df['total_value'].iloc[-1]
                    fin_inf = res_df['inflation_principal'].iloc[-1]
                    
                    prof = (fin_val - fin_inv) / fin_inv * 100
                    exc = fin_val - fin_inf
                    mdd = calculate_mdd(res_df['total_value'])
                    
                    x_dates = [d for d in bi if d <= df.index.max()] + [res_df['date'].iloc[-1]]
                    x_flows = [-pt_krw]*len([d for d in bi if d <= df.index.max()]) + [fin_val]
                    # xirr 길이 보정
                    if len(x_dates) > len(x_flows): x_dates = x_dates[:len(x_flows)]
                    elif len(x_flows) > len(x_dates): x_flows = x_flows[:len(x_dates)]
                    
                    try: xv = xirr(x_flows, x_dates) * 100
                    except: xv = 0.0
                    
                    # AI 분석 (여기서 미리 생성해서 저장)
                    ai_txt = "AI 분석 미사용"
                    if ai and GEMINI_API_KEY:
                        prompt = f"""종목:{iq}, 기간:{yrs}년, 원금:{fin_inv:,.0f}, 최종:{fin_val:,.0f}, 수익률:{prof:.2f}%, MDD:{mdd:.2f}%. 분석요약."""
                        try: ai_txt = genai.GenerativeModel("gemini-pro").generate_content(prompt).text
                        except: ai_txt = "AI 호출 실패"
                    
                    # 결과 Session State에 저장
                    st.session_state['sim_result'] = {
                        'df': res_df, 'iq': iq, 'inv': fin_inv, 'val': fin_val, 'prof': prof, 
                        'exc': exc, 'xv': xv, 'mdd': mdd, 'ai': ai_txt, 'dates': x_dates
                    }
                else: st.error("데이터 없음")

            # 결과 표시 (Session State 기반)
            if 'sim_result' in st.session_state:
                res = st.session_state['sim_result']
                st.divider()
                st.subheader(f"📊 {res['iq']} 분석 결과")
                
                # 단위 선택 (이것이 바뀌어도 if 'sim_result' 블록 안에 있으므로 데이터 유지됨)
                u_opt = st.radio("단위", ["원", "만원", "백만원", "억원"], horizontal=True)
                div_map = {"원":1, "만원":10000, "백만원":1000000, "억원":100000000}
                divider = div_map[u_opt]
                
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("총 투자원금", format_currency(res['inv'], u_opt))
                c2.metric("최종 평가액", format_currency(res['val'], u_opt))
                c3.metric("수익률 / XIRR", f"{res['prof']:.1f}% / {res['xv']:.1f}%")
                c4.metric("초과수익[최종 평가액 - 물가상승(2%)]", format_currency(res['exc'], u_opt))
                
                # MDD 표시
                st.caption(f"📉 최대 낙폭 (MDD): **{res['mdd']:.2f}%**")
                
                # 차트 생성 (단위 적용, 26회차 마커)
                chart_buf = create_chart(res['df'], res['iq'], divider, u_opt)
                st.image(chart_buf, use_container_width=True)
                
                if res['ai'] != "AI 분석 미사용": st.info(res['ai'])
                
                # PDF
                pdf_d = create_pdf(res['iq'], res['ai'], res['prof'], res['xv'], res['inv'], res['val'], res['exc'], chart_buf, res['mdd'])
                st.download_button("📄 PDF 다운로드", pdf_d, f"{res['iq']}_report.pdf", "application/pdf")

        with tab2:
            st.subheader("내 보유 자산")
            df_p = get_portfolio_df(user_email)
            if not df_p.empty:
                s = df_p.groupby('ticker').agg(q=('quantity','sum'), i=('price', lambda x: (x*df_p.loc[x.index, 'quantity']).sum())).reset_index()
                ts = s['ticker'].tolist()
                try:
                    cd = yf.download(ts, period='1d', group_by='ticker', progress=False)
                    cur_p = {}
                    for t in ts:
                        try: 
                            if len(ts) > 1: cur_p[t] = float(cd.iloc[-1][(t, 'Close')])
                            else: cur_p[t] = float(cd.iloc[-1]['Close'])
                        except: cur_p[t] = 0
                    s['c'] = s['ticker'].map(cur_p)
                except: s['c'] = 0
                s['v'] = s['c']*s['q']; s['r'] = (s['v']-s['i'])/s['i']*100
                d_df = s.rename(columns={'ticker':'종목','q':'수량','i':'매수금','c':'현재가','v':'평가액','r':'수익률'})
                st.dataframe(d_df.style.format({'매수금':"{:,.0f}",'현재가':"{:,.0f}",'평가액':"{:,.0f}",'수익률':"{:.2f}%"}))
            
            with st.form("add"):
                c1,c2 = st.columns(2)
                t = c1.text_input("종목코드"); d = c2.date_input("날짜")
                c3,c4 = st.columns(2)
                p = c3.text_input("단가"); q = c4.text_input("수량")
                if st.form_submit_button("추가"):
                    try: add_trade(user_email, t, d, float(p.replace(",","")), int(q.replace(",",""))); st.rerun()
                    except: pass

if __name__ == "__main__":
    if "token" not in st.session_state: show_landing_page()
    else: show_main_app()
