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
    if "private_key" in creds_dict:
        creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n").strip()
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    return gspread.authorize(creds)

def get_sheet(sheet_name):
    client = init_connection()
    try:
        return client.open("portfolio_db").worksheet(sheet_name)
    except:
        # 시트가 없으면 생성 시도 (첫 사용자 편의)
        sh = client.open("portfolio_db")
        ws = sh.add_worksheet(title=sheet_name, rows=100, cols=10)
        return ws

# --- 사용자 설정(프로필) 관련 함수 ---
def get_user_info(email):
    """이메일로 사용자 정보 조회 (없으면 기본값 반환)"""
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
    except Exception as e:
        pass
    return {"nickname": "투자자", "name": "", "default_budget": 1000000}

def update_user_info(email, nickname, name, budget):
    """사용자 정보 저장 (업데이트 또는 추가)"""
    try:
        sheet = get_sheet("user_settings")
        records = sheet.get_all_records()
        df = pd.DataFrame(records)
        
        # 데이터프레임에서 해당 이메일 행 찾기
        if not df.empty and email in df['email'].values:
            # 기존 회원: 해당 행 찾아서 업데이트 (gspread cell update 사용)
            cell = sheet.find(email)
            sheet.update_cell(cell.row, 2, nickname)
            sheet.update_cell(cell.row, 3, name)
            sheet.update_cell(cell.row, 4, budget)
        else:
            # 신규 회원: 행 추가
            if not records: # 헤더가 없으면 추가
                sheet.append_row(["email", "nickname", "name", "default_budget"])
            sheet.append_row([email, nickname, name, budget])
        return True
    except Exception as e:
        st.error(f"저장 실패: {e}")
        return False

# --- 포트폴리오 DB 관련 함수 ---
def add_trade(user_email, ticker, date, price, quantity):
    try:
        sheet = get_sheet("sheet1") # 기본 시트
        # 헤더 체크
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

@st.cache_data
def load_data(ticker):
    try:
        data = yf.download(ticker, period="max")
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)
        return data
    except: return None

def format_number(num):
    """천 단위 콤마 포맷팅 (입력용)"""
    if num:
        return "{:,}".format(int(num))
    return "0"

# ---------------------------------------------------------
# 4. 화면 구성
# ---------------------------------------------------------
def show_landing_page():
    st.markdown("<h1 style='text-align: center; color: #1E88E5;'>🚀 AI Stock DCA Master</h1>", unsafe_allow_html=True)
    st.info("로그인하여 당신의 투자를 시작하세요.")
    col1, col2, col3 = st.columns([1,2,1])
    with col2:
        if CLIENT_ID and CLIENT_SECRET:
            oauth2 = OAuth2Component(CLIENT_ID, CLIENT_SECRET, AUTHORIZE_URL, TOKEN_URL, REVOKE_TOKEN_URL, REVOKE_TOKEN_URL)
            result = oauth2.authorize_button("Google 계정으로 계속하기", REDIRECT_URI, SCOPE, key="google_auth", use_container_width=True)
            if result:
                st.session_state["token"] = result.get("token")
                st.session_state["user_email"] = result.get("id_token", {}).get("email")
                st.rerun()

def show_main_app():
    user_email = st.session_state.get("user_email")
    
    # 사용자 정보 로드 (세션에 없으면 DB에서 가져옴)
    if "user_info" not in st.session_state:
        st.session_state["user_info"] = get_user_info(user_email)
    
    user_info = st.session_state["user_info"]
    nickname = user_info.get("nickname", "투자자")

    # --- 사이드바 ---
    with st.sidebar:
        st.title(f"반가워요, {nickname}님! 👋")
        
        # 메뉴 선택
        menu = st.radio("메뉴 이동", ["📊 시뮬레이션 & 포트폴리오", "⚙️ 회원 정보 수정"])
        
        st.divider()
        if st.button("로그아웃"):
            del st.session_state["token"]
            if "user_info" in st.session_state: del st.session_state["user_info"]
            st.rerun()

    # --- 메인 화면: 회원 정보 수정 ---
    if menu == "⚙️ 회원 정보 수정":
        st.header("⚙️ 회원 정보 수정")
        st.write("여기서 설정한 **월 예산**은 시뮬레이션 시 기본값으로 사용됩니다.")
        
        with st.form("profile_form"):
            new_nick = st.text_input("닉네임", value=user_info.get("nickname", ""))
            new_name = st.text_input("이름", value=user_info.get("name", ""))
            
            # 콤마 입력을 위한 텍스트 처리 로직
            current_budget = user_info.get("default_budget", 1000000)
            budget_str = st.text_input("매월 투자 예산 (원 또는 달러)", value=format_number(current_budget))
            
            if st.form_submit_button("저장하기"):
                # 콤마 제거 후 숫자로 변환
                try:
                    clean_budget = int(budget_str.replace(",", ""))
                except:
                    clean_budget = 0
                
                if update_user_info(user_email, new_nick, new_name, clean_budget):
                    # 세션 상태 업데이트
                    st.session_state["user_info"] = {
                        "nickname": new_nick,
                        "name": new_name,
                        "default_budget": clean_budget
                    }
                    st.success("정보가 저장되었습니다!")
                    time.sleep(1)
                    st.rerun()

    # --- 메인 화면: 시뮬레이션 & 포트폴리오 ---
    elif menu == "📊 시뮬레이션 & 포트폴리오":
        st.title("💰 AI Stock DCA Master")
        
        # [설정 패널] - 사이드바 대신 상단 확장형으로 배치하거나 컬럼으로 배치
        with st.expander("🛠 **시뮬레이션 설정 열기** (여기서 종목과 금액을 설정하세요)", expanded=True):
            c1, c2, c3 = st.columns(3)
            with c1:
                input_ticker = get_ticker(st.text_input("종목명 또는 코드", "삼성전자"))
            with c2:
                # 콤마 입력을 위해 text_input 사용 후 변환
                default_b = user_info.get("default_budget", 1000000)
                budget_input = st.text_input("매월 투자 예산 (원 또는 달러)", value=format_number(default_b))
                try:
                    monthly_budget = int(budget_input.replace(",", ""))
                except:
                    monthly_budget = 0
                    st.error("숫자만 입력해주세요.")
            with c3:
                interval_type = st.radio("매수 주기", ["매월", "매주", "매일"], horizontal=True)

            # 주기별 세부 옵션
            c4, c5 = st.columns([1, 2])
            with c4:
                target_day, target_date = None, None
                if interval_type == "매주":
                    target_day = st.selectbox("요일", ["월요일", "화요일", "수요일", "목요일", "금요일"], index=4)
                elif interval_type == "매월":
                    target_date = st.selectbox("날짜", [1, 15, 30], index=0)

        # 탭 구성
        tab1, tab2 = st.tabs(["📈 DCA 백테스팅", "💼 내 포트폴리오"])

        # [TAB 1] 백테스팅
        with tab1:
            if st.button("🚀 백테스팅 및 AI 분석 시작", type="primary"):
                raw_data = load_data(input_ticker)
                if raw_data is not None and not raw_data.empty:
                    # 데이터 기간 및 슬라이더 (자동 3년 설정)
                    end_d = raw_data.index.max().date()
                    start_d = raw_data.index.min().date()
                    years_avail = (end_d - start_d).days // 365
                    test_period = 3 if years_avail >= 3 else years_avail
                    
                    df = raw_data.last(f"{test_period}Y").copy()
                    
                    # 매수 주기별 필터링
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

                    # 투자 금액 계산
                    per_trade = monthly_budget
                    if interval_type == "매주": per_trade = monthly_budget * 12 / 52
                    elif interval_type == "매일": per_trade = monthly_budget * 12 / 250

                    st.info(f"💡 월 예산 **{format_number(monthly_budget)}원** 기준 ➡️ 1회 약 **{format_number(per_trade)}원** 투자")

                    # 로직 수행
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

                    # 결과 출력
                    m1, m2, m3 = st.columns(3)
                    m1.metric("총 투자금", f"{format_number(total_invested)}원")
                    m2.metric("최종 평가액", f"{format_number(final_val)}원")
                    m3.metric("수익률", f"{profit_rate:.2f}%")
                    st.line_chart(balance_history)
                    
                    # AI 분석
                    with st.spinner("🤖 AI 분석 중..."):
                        if GEMINI_API_KEY:
                            model = genai.GenerativeModel('gemini-1.5-flash')
                            prompt = f"종목:{input_ticker},기간:{test_period}년,수익률:{profit_rate:.2f}%. 분석해줘."
                            try:
                                res = model.generate_content(prompt).text
                                st.success(res)
                            except: st.error("AI 분석 오류")
                else:
                    st.error("데이터 로드 실패")

        # [TAB 2] 포트폴리오 관리 (삭제 기능 제외, 단순 입력/조회)
        with tab2:
            st.subheader("내 보유 자산 현황")
            df_port = get_portfolio_df(user_email)
            
            if not df_port.empty:
                # 현재가 조회 및 수익률 계산
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
                
                # 포맷팅하여 표시
                disp = summ.copy()
                disp['평단가'] = disp['inv'] / disp['qty']
                disp = disp[['ticker', 'qty', '평단가', 'cur', 'rate']]
                disp.columns = ['종목', '보유수량', '평단가', '현재가', '수익률(%)']
                
                st.dataframe(disp.style.format({
                    '평단가': "{:,.0f}", '현재가': "{:,.0f}", '수익률(%)': "{:.2f}%"
                }))
            else:
                st.info("아직 투자 기록이 없습니다.")

            st.divider()
            st.subheader("📝 매수 기록 추가")
            with st.form("trade_add"):
                c1, c2 = st.columns(2)
                t = c1.text_input("종목 코드", input_ticker)
                d = c2.date_input("날짜")
                
                c3, c4 = st.columns(2)
                # 여기도 콤마 입력 적용
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
                    except:
                        st.error("숫자 형식을 확인해주세요.")

# ---------------------------------------------------------
# 5. 실행 제어
# ---------------------------------------------------------
if "token" not in st.session_state:
    show_landing_page()
else:
    show_main_app()
