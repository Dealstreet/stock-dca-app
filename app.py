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
st.set_page_config(page_title="AI Stock DCA Master", layout="wide", page_icon="📈")

# Streamlit Secrets에서 설정 불러오기
GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY")
CLIENT_ID = st.secrets.get("GOOGLE_CLIENT_ID")
CLIENT_SECRET = st.secrets.get("GOOGLE_CLIENT_SECRET")
REDIRECT_URI = st.secrets.get("REDIRECT_URI")

# Google OAuth 설정
AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_TOKEN_URL = "https://oauth2.googleapis.com/revoke"
SCOPE = "openid email profile"

# Gemini 설정
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# ---------------------------------------------------------
# 2. 구글 시트 DB 연결 및 관리 함수
# ---------------------------------------------------------
@st.cache_resource
def init_connection():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    
    # Secrets 데이터를 복사본으로 가져옴
    creds_dict = dict(st.secrets["gcp_service_account"])
    
    # [수정 포인트 1] 역슬래시 n(\n) 처리
    if "private_key" in creds_dict:
        creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
        
        # [수정 포인트 2] 양 끝 공백 제거 및 Base64 패딩 문제 해결
        creds_dict["private_key"] = creds_dict["private_key"].strip()
        
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    return gspread.authorize(creds)

def get_db_sheet():
    """portfolio_db 시트 객체 반환"""
    client = init_connection()
    # 주의: 구글 드라이브에 'portfolio_db'라는 이름의 시트가 있어야 하며, 서비스 계정에 공유되어 있어야 함
    return client.open("portfolio_db").sheet1

def init_db():
    """DB(시트) 초기화: 헤더가 없으면 생성"""
    try:
        sheet = get_db_sheet()
        # 시트가 비어있으면 헤더 추가
        if not sheet.get_all_records():
            sheet.append_row(["user_email", "ticker", "date", "price", "quantity"])
    except Exception as e:
        st.error(f"⚠️ 구글 시트 연결 오류: {e}\n\n'portfolio_db' 시트가 생성되어 있고, 서비스 계정에 공유되었는지 확인해주세요.")

def add_trade(user_email, ticker, date, price, quantity):
    """매매 기록 추가"""
    try:
        sheet = get_db_sheet()
        sheet.append_row([user_email, ticker, str(date), price, int(quantity)])
    except Exception as e:
        st.error(f"데이터 저장 실패: {e}")

def get_portfolio_df(user_email):
    """특정 사용자의 포트폴리오 데이터 조회"""
    try:
        sheet = get_db_sheet()
        data = sheet.get_all_records()
        df = pd.DataFrame(data)
        
        if not df.empty:
            # 숫자형 데이터 변환
            df['price'] = pd.to_numeric(df['price'])
            df['quantity'] = pd.to_numeric(df['quantity'])
            # 현재 로그인한 사용자의 데이터만 필터링
            return df[df['user_email'] == user_email]
        return pd.DataFrame()
    except Exception as e:
        # 데이터가 없거나 오류 발생 시 빈 DataFrame 반환
        return pd.DataFrame()

# 앱 실행 시 DB 연결 상태 체크
init_db()

# ---------------------------------------------------------
# 3. 유틸리티 함수 (종목 매핑, 데이터 로드, AI, PDF)
# ---------------------------------------------------------
def get_ticker(query):
    """종목명/코드를 티커로 변환"""
    query = query.strip()
    mapping = {
        "삼성전자": "005930.KS", "SK하이닉스": "000660.KS", "LG에너지솔루션": "373220.KS",
        "현대차": "005380.KS", "NAVER": "035420.KS", "카카오": "035720.KS",
        "애플": "AAPL", "테슬라": "TSLA", "엔비디아": "NVDA", "마이크로소프트": "MSFT",
        "구글": "GOOGL", "아마존": "AMZN", "비트코인": "BTC-USD",
        "나스닥100": "QQQ", "S&P500": "SPY", "배당성장": "SCHD", "반도체": "SOXL"
    }
    if query in mapping: return mapping[query]
    if query.isdigit() and len(query) == 6: return f"{query}.KS"
    return query

@st.cache_data
def load_data(ticker):
    """주가 데이터 로드 (캐싱)"""
    try:
        data = yf.download(ticker, period="max")
        # MultiIndex 컬럼 처리 (yfinance 최신 버전 대응)
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)
        return data
    except: return None

def get_ai_analysis(ticker, profit_rate, total_invested, final_value, period):
    """Gemini AI 투자 분석"""
    if not GEMINI_API_KEY:
        return "⚠️ Gemini API 키가 설정되지 않았습니다."
    
    model = genai.GenerativeModel('gemini-1.5-flash')
    prompt = f"""
    당신은 전문 금융 투자 자문가입니다. 아래 DCA(적립식 투자) 시뮬레이션 결과에 대해 300자 내외로 분석 리포트를 작성해주세요.
    
    [투자 정보]
    - 종목: {ticker}
    - 기간: {period}년
    - 총 투자금: {total_invested:,.0f}원
    - 최종 평가액: {final_value:,.0f}원
    - 수익률: {profit_rate:.2f}%
    
    [요청 사항]
    1. 수익률에 대한 객관적 평가 (긍정/부정)
    2. DCA 전략이 이 종목의 변동성에 효과적이었는지 분석
    3. 향후 투자자에 대한 한 줄 조언
    - 말투는 정중하게 '합니다' 체로 작성해줘.
    """
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"AI 분석 중 오류가 발생했습니다: {e}"

def create_pdf(ticker, analysis_text, profit_rate, total_invested, final_value):
    """PDF 리포트 생성"""
    pdf = FPDF()
    pdf.add_page()
    
    # 한글 폰트 설정 (GitHub에 NanumGothic.ttf 파일 필수)
    font_path = "NanumGothic.ttf" 
    if os.path.exists(font_path):
        pdf.add_font('Nanum', '', font_path, uni=True)
        pdf.set_font('Nanum', '', 12)
    else:
        pdf.set_font("Arial", size=12)
        pdf.cell(0, 10, txt="Error: Korean font (NanumGothic.ttf) not found.", ln=True)

    # 제목
    pdf.set_font_size(16)
    pdf.cell(0, 10, txt=f"[{ticker}] DCA Investment Report", ln=True, align='C')
    pdf.ln(10)
    
    # 요약 데이터
    pdf.set_font_size(12)
    pdf.cell(0, 10, txt=f"Total Invested: {total_invested:,.0f}", ln=True)
    pdf.cell(0, 10, txt=f"Final Value: {final_value:,.0f}", ln=True)
    
    # 수익률 색상 처리 (수익: 파랑, 손실: 빨강)
    color = (255, 0, 0) if profit_rate < 0 else (0, 0, 255)
    pdf.set_text_color(*color)
    pdf.cell(0, 10, txt=f"Profit Rate: {profit_rate:.2f}%", ln=True)
    pdf.set_text_color(0, 0, 0) # 색상 초기화
    pdf.ln(10)
    
    # AI 분석 내용
    pdf.multi_cell(0, 8, txt=analysis_text)
    
    return pdf.output(dest='S').encode('latin-1')

# ---------------------------------------------------------
# 4. 화면 구성: 랜딩 페이지 (로그인 전)
# ---------------------------------------------------------
def show_landing_page():
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
    
    # 구글 로그인 버튼
    col_centered = st.columns([1, 2, 1])
    with col_centered[1]:
        st.markdown("<h3 style='text-align: center;'>지금 바로 시작하기</h3>", unsafe_allow_html=True)
        if CLIENT_ID and CLIENT_SECRET:
            oauth2 = OAuth2Component(CLIENT_ID, CLIENT_SECRET, AUTHORIZE_URL, TOKEN_URL, REVOKE_TOKEN_URL, REVOKE_TOKEN_URL)
            result = oauth2.authorize_button(
                name="Continue with Google",
                icon="https://www.google.com.tw/favicon.ico",
                redirect_uri=REDIRECT_URI,
                scope=SCOPE,
                key="google_auth",
                use_container_width=True,
            )
            if result:
                st.session_state["token"] = result.get("token")
                st.session_state["user_email"] = result.get("id_token", {}).get("email")
                st.rerun()
        else:
            st.error("⚠️ Google Client ID/Secret 설정이 필요합니다. (secrets.toml 확인)")
            # 개발용 임시 버튼 (배포 시 삭제 가능)
            if st.button("임시 로그인 (테스트용)", use_container_width=True):
                st.session_state["token"] = {"access_token": "dev_token"}
                st.session_state["user_email"] = "test_user@example.com"
                st.rerun()

# ---------------------------------------------------------
# 5. 화면 구성: 메인 앱 (로그인 후)
# ---------------------------------------------------------
def show_main_app():
    user_email = st.session_state.get("user_email", "User")
    
    # [사이드바]
    with st.sidebar:
        st.write(f"안녕하세요, **{user_email}**님! 👋")
        if st.button("로그아웃"):
            del st.session_state["token"]
            st.rerun()
        st.divider()
        
        st.header("🛠 시뮬레이션 설정")
        input_ticker_name = st.text_input("종목명 또는 코드", "삼성전자")
        ticker = get_ticker(input_ticker_name)
        
        monthly_budget = st.number_input("매월 투자 예산 (원/달러)", value=1000000, step=10000)
        
        interval_type = st.radio("매수 주기", ["매월", "매주", "매일"])
        target_day, target_date = None, None
        
        if interval_type == "매주":
            target_day = st.selectbox("요일 선택", ["월요일", "화요일", "수요일", "목요일", "금요일"], index=4)
        elif interval_type == "매월":
            target_date = st.selectbox("날짜 선택", [1, 15, 30], index=0)

    # [메인 화면]
    st.title("💰 AI Stock DCA Master")
    
    tab1, tab2 = st.tabs(["📊 DCA 백테스팅 & AI 분석", "💼 내 포트폴리오 관리"])

    # --- TAB 1: 백테스팅 ---
    with tab1:
        raw_data = load_data(ticker)
        
        if raw_data is not None and not raw_data.empty:
            start_d = raw_data.index.min().date()
            end_d = raw_data.index.max().date()
            
            st.info(f"📅 **{input_ticker_name}({ticker})** 데이터 기간: {start_d} ~ {end_d}")
            
            years_avail = (end_d - start_d).days // 365
            test_period = st.slider("백테스팅 기간 (년)", 1, max(1, years_avail), min(3, max(1, years_avail)))
            
            # 1회 매수 금액 자동 계산
            per_trade_amount = 0
            if interval_type == "매월": per_trade_amount = monthly_budget
            elif interval_type == "매주": per_trade_amount = monthly_budget * 12 / 52
            elif interval_type == "매일": per_trade_amount = monthly_budget * 12 / 250
            
            st.write(f"💡 월 예산 **{monthly_budget:,.0f}원** 기준 ➡️ 1회 약 **{per_trade_amount:,.0f}원** 투자")

            if st.button("🚀 백테스팅 및 AI 분석 시작", key="btn_run_backtest"):
                # 데이터 필터링
                df = raw_data.last(f"{test_period}Y").copy()
                buy_indices = []
                
                # 매수 시점 계산
                if interval_type == "매일":
                    buy_indices = df.index
                elif interval_type == "매주":
                    day_map = {"월요일":0,"화요일":1,"수요일":2,"목요일":3,"금요일":4}
                    buy_indices = df[df.index.weekday == day_map[target_day]].index
                elif interval_type == "매월":
                    grouped = df.groupby([df.index.year, df.index.month])
                    for _, group in grouped:
                        candidates = group[group.index.day >= target_date]
                        if not candidates.empty:
                            buy_indices.append(candidates.index[0])
                        else:
                            buy_indices.append(group.index[-1])
                
                # DCA 로직 수행
                total_invested = 0
                total_shares = 0
                balance_history = []
                
                for date, row in df.iterrows():
                    if date in buy_indices:
                        shares_to_buy = per_trade_amount // row['Close']
                        if shares_to_buy > 0:
                            total_invested += shares_to_buy * row['Close']
                            total_shares += shares_to_buy
                    balance_history.append(total_shares * row['Close'])
                
                # 결과 계산
                final_value = total_shares * df['Close'].iloc[-1]
                profit = final_value - total_invested
                profit_rate = (profit / total_invested * 100) if total_invested > 0 else 0
                
                # 지표 출력
                c1, c2, c3 = st.columns(3)
                c1.metric("총 투자 원금", f"{total_invested:,.0f}원")
                c2.metric("최종 평가 금액", f"{final_value:,.0f}원")
                c3.metric("수익률", f"{profit_rate:.2f}%")
                
                # 차트 출력
                st.line_chart(balance_history)
                
                # AI 분석 및 PDF
                st.divider()
                with st.spinner("🤖 Gemini가 투자 결과를 분석 중입니다..."):
                    ai_response = get_ai_analysis(ticker, profit_rate, total_invested, final_value, test_period)
                    st.subheader("Gemini 투자 분석 리포트")
                    st.info(ai_response)
                    
                    pdf_data = create_pdf(ticker, ai_response, profit_rate, total_invested, final_value)
                    st.download_button(
                        label="📄 PDF 리포트 다운로드",
                        data=pdf_data,
                        file_name=f"{ticker}_DCA_Report.pdf",
                        mime="application/pdf"
                    )
        else:
            st.error("데이터를 불러올 수 없습니다. 종목명이나 코드를 확인해주세요.")

    # --- TAB 2: 포트폴리오 관리 ---
    with tab2:
        sub_tab1, sub_tab2 = st.tabs(["📊 대시보드 (자동 분석)", "📝 매매일지 작성 (수동 입력)"])
        
        # [서브탭 2] 매매일지 작성
        with sub_tab2:
            st.subheader("매수 기록 추가")
            with st.form("add_trade_form"):
                c1, c2 = st.columns(2)
                input_t = c1.text_input("종목 코드 (예: 005930.KS)", value=ticker)
                input_d = c2.date_input("매수 일자")
                
                c3, c4 = st.columns(2)
                input_p = c3.number_input("매수 단가", min_value=1)
                input_q = c4.number_input("매수 수량", min_value=1, step=1)
                
                if st.form_submit_button("기록 저장"):
                    add_trade(user_email, input_t, input_d, input_p, input_q)
                    st.success("✅ 매수 기록이 구글 시트에 안전하게 저장되었습니다.")
                    time.sleep(1)
                    st.rerun()

        # [서브탭 1] 대시보드
        with sub_tab1:
            st.subheader("내 보유 자산 현황")
            df_port = get_portfolio_df(user_email)
            
            if df_port.empty:
                st.info("아직 기록된 매매 내역이 없습니다. '매매일지 작성' 탭에서 첫 매수를 기록해보세요!")
            else:
                # 데이터 가공: 종목별 합계
                summary = df_port.groupby('ticker').agg(
                    total_qty=('quantity', 'sum'),
                    total_invested=('price', lambda x: (x * df_port.loc[x.index, 'quantity']).sum())
                ).reset_index()
                
                # 현재가 조회 (yfinance)
                tickers_list = summary['ticker'].tolist()
                current_prices_map = {}
                
                try:
                    if tickers_list:
                        # 일괄 조회로 속도 최적화
                        live_data = yf.download(tickers_list, period="1d")['Close'].iloc[-1]
                        
                        for t in tickers_list:
                            if len(tickers_list) == 1:
                                current_prices_map[t] = float(live_data)
                            else:
                                current_prices_map[t] = float(live_data[t])
                except Exception as e:
                    st.warning(f"현재가 조회 중 일부 오류 발생: {e}")
                
                # 지표 계산
                summary['current_price'] = summary['ticker'].map(current_prices_map).fillna(0)
                summary['current_val'] = summary['current_price'] * summary['total_qty']
                summary['profit_rate'] = (summary['current_val'] - summary['total_invested']) / summary['total_invested'] * 100
                summary['avg_price'] = summary['total_invested'] / summary['total_qty']
                
                # 전체 요약
                total_asset = summary['current_val'].sum()
                total_invest = summary['total_invested'].sum()
                total_profit_rate = ((total_asset - total_invest) / total_invest * 100) if total_invest > 0 else 0
                
                m1, m2, m3 = st.columns(3)
                m1.metric("내 총 자산", f"{total_asset:,.0f}원")
                m2.metric("총 투자 원금", f"{total_invest:,.0f}원")
                m3.metric("통합 수익률", f"{total_profit_rate:.2f}%", delta=f"{total_asset - total_invest:,.0f}원")
                
                st.divider()
                
                # 상세 테이블
                display_df = summary[['ticker', 'total_qty', 'avg_price', 'current_price', 'profit_rate']].copy()
                display_df.columns = ['종목', '보유수량', '평단가', '현재가', '수익률(%)']
                
                st.dataframe(
                    display_df.style.format({
                        '평단가': "{:,.0f}", 
                        '현재가': "{:,.0f}", 
                        '수익률(%)': "{:.2f}%"
                    }).background_gradient(subset=['수익률(%)'], cmap='RdYlGn', vmin=-20, vmax=20)
                )

# ---------------------------------------------------------
# 6. 메인 실행 제어
# ---------------------------------------------------------
if "token" not in st.session_state:
    show_landing_page()
else:
    show_main_app()
