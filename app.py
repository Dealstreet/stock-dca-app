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
import datetime
from io import BytesIO
import numpy as np

# ---------------------------------------------------------
# 1. 앱 페이지 설정 및 기본 설정
# ---------------------------------------------------------
st.set_page_config(page_title="AI Stock DCA Master Pro", layout="wide", page_icon="📈")

GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY")
CLIENT_ID = st.secrets.get("GOOGLE_CLIENT_ID")
CLIENT_SECRET = st.secrets.get("GOOGLE_CLIENT_SECRET")
REDIRECT_URI = st.secrets.get("REDIRECT_URI")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# ---------------------------------------------------------
# 2. 헬퍼 함수 (데이터, 계산, 시각화)
# ---------------------------------------------------------

# 폰트 설정 (한글 깨짐 방지)
def set_korean_font():
    font_path = "NanumGothic-Regular.ttf"
    if not os.path.exists(font_path):
        urllib.request.urlretrieve("https://github.com/Dealstreet/stock-dca-app/raw/refs/heads/main/NanumGothic-Regular.ttf", font_path)
    font_prop = fm.FontProperties(fname=font_path)
    plt.rcParams['font.family'] = font_prop.get_name()
    plt.rcParams['axes.unicode_minus'] = False
    return font_prop

# 환율 정보 가져오기 (1달러당 원화)
@st.cache_data(ttl=3600)
def get_exchange_rate():
    try:
        df = yf.download("KRW=X", period="1d", progress=False)
        if not df.empty:
            return float(df['Close'].iloc[-1])
    except:
        pass
    return 1400.0 # 기본값

# 티커 매핑 및 검색
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

# 데이터 로드 (배당금 포함)
@st.cache_data(ttl=3600)
def load_data(ticker):
    try:
        # actions=True로 배당금/액면분할 정보 포함
        ticker_obj = yf.Ticker(ticker)
        data = ticker_obj.history(period="max")
        if not data.empty:
            # 시간대 제거 (날짜 비교 편의성)
            data.index = data.index.tz_localize(None)
            return data
    except Exception as e:
        st.error(f"데이터 다운로드 실패: {e}")
    return None

# XIRR 계산 함수
def xirr(cashflows, dates):
    if len(cashflows) != len(dates): return None
    
    def npv(rate):
        if rate <= -1.0: return float('inf')
        d0 = dates[0]
        return sum([cf / ((1 + rate) ** ((d - d0).days / 365.0)) for cf, d in zip(cashflows, dates)])
    
    try:
        return optimize.newton(npv, 0.1)
    except:
        return None

# 숫자 포맷팅 (단위 변환)
def format_currency(value, unit="원"):
    if unit == "만원":
        return f"{value/10000:,.0f}만원"
    elif unit == "백만원":
        return f"{value/1000000:,.2f}백만원"
    elif unit == "억원":
        return f"{value/100000000:,.4f}억원"
    else:
        return f"{value:,.0f}원"

# 차트 생성 (Matplotlib)
def create_chart(df_history, ticker_name):
    font_prop = set_korean_font()
    
    fig, ax = plt.subplots(figsize=(10, 6))
    
    dates = df_history['date']
    
    # 1. 포트폴리오 가치
    ax.plot(dates, df_history['total_value'], label='포트폴리오 가치', color='#FF5733', linewidth=2, 
            marker='o', markevery=10, markersize=5) # 10회차마다 점
    
    # 2. 총 투자원금
    ax.plot(dates, df_history['invested'], label='총 투자원금', color='#333333', linestyle='--', linewidth=1.5)
    
    # 3. 물가상승 반영 원금선 (연 2%)
    ax.plot(dates, df_history['inflation_principal'], label='물가상승원금선 (연2%)', color='#2E86C1', linestyle=':', linewidth=1.5)
    
    ax.set_title(f"[{ticker_name}] DCA 투자 성과 추이", fontproperties=font_prop, fontsize=16)
    ax.set_xlabel("기간 (월)", fontproperties=font_prop)
    ax.set_ylabel("평가 금액", fontproperties=font_prop)
    
    # X축 월별 표시
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=max(1, len(dates)//10))) # 너무 촘촘하지 않게 조정
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    plt.xticks(rotation=45)
    
    ax.legend(prop=font_prop)
    ax.grid(True, linestyle='--', alpha=0.5)
    
    plt.tight_layout()
    
    # 이미지를 메모리에 저장 (PDF 및 화면 표시용)
    buf = BytesIO()
    plt.savefig(buf, format="png", dpi=100)
    buf.seek(0)
    plt.close(fig)
    return buf

# PDF 생성 함수 (차트 포함)
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
    else:
        pdf.set_font('Arial', 'B', 20)
        
    pdf.cell(0, 15, txt=f"[{ticker}] Investment Report", ln=True, align='C')
    pdf.ln(5)
    
    if has_korean: pdf.set_font('Nanum', '', 12)
    else: pdf.set_font('Arial', '', 12)
    
    # 핵심 지표
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(0, 10, txt=f" Total Invested (Principal): {total_invested:,.0f} KRW", ln=True, fill=True)
    pdf.cell(0, 10, txt=f" Final Portfolio Value: {final_value:,.0f} KRW", ln=True, fill=True)
    pdf.cell(0, 10, txt=f" Simple Return: {profit_rate:.2f}% | XIRR: {xirr_val:.2f}%", ln=True, fill=True)
    pdf.cell(0, 10, txt=f" Excess Return (vs Inflation 2%): {excess_return:,.0f} KRW", ln=True, fill=True)
    pdf.ln(10)
    
    # 차트 이미지 삽입
    if chart_buf:
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmpfile:
            tmpfile.write(chart_buf.getvalue())
            tmp_path = tmpfile.name
        # 페이지 너비에 맞게 조정 (A4 너비 약 210mm, 여백 고려 190mm)
        pdf.image(tmp_path, x=10, w=190)
        os.unlink(tmp_path)
    pdf.ln(10)
    
    pdf.multi_cell(0, 8, txt=analysis_text)
    
    return pdf.output(dest='S').encode('latin-1')

# ---------------------------------------------------------
# 3. 메인 앱 로직
# ---------------------------------------------------------

def show_main_app():
    # ... (기존 로그인/사이드바 로직과 동일, 생략 없이 필요하다면 이전 코드 참조) ...
    # 편의상 핵심인 "시뮬레이션" 탭 부분만 집중적으로 수정하여 보여드립니다.
    
    st.title("💰 AI Stock DCA Master Pro")
    
    # --- 설정 패널 ---
    with st.expander("🛠 **시뮬레이션 고급 설정**", expanded=True):
        c1, c2, c3 = st.columns(3)
        with c1: 
            input_query = st.text_input("종목명 또는 코드", "삼성전자")
            input_ticker = get_ticker(input_query)
        with c2:
            budget_str = st.text_input("매월 투자 예산", "1,000,000")
            try: monthly_budget = int(budget_str.replace(",", "").replace("원", ""))
            except: monthly_budget = 0
        with c3:
            interval_type = st.selectbox("매수 주기", ["매월", "매주", "매일"])

        c4, c5, c6 = st.columns(3)
        with c4:
            years = st.slider("기간 (년)", 1, 10, 3)
        with c5:
            # 배당금 재투자 여부 버튼
            use_dividend = st.checkbox("배당금 재투자 (TR 효과)", value=True)
        with c6:
            ai_use = st.checkbox("AI 투자 분석 리포트 생성", value=False)
            
        c7, c8 = st.columns([2,1])
        with c7:
            # 환율 정보 표시
            usd_krw = get_exchange_rate()
            st.caption(f"ℹ️ 현재 환율 적용: 1 USD = {usd_krw:,.2f} KRW (해외 주식일 경우 자동 계산)")

    # --- 실행 ---
    if st.button("🚀 시뮬레이션 시작", type="primary"):
        # 데이터 로드
        raw_data = load_data(input_ticker)
        
        if raw_data is None or raw_data.empty:
            st.error("데이터를 불러올 수 없습니다.")
            return

        # 통화 판별 (KRW vs USD)
        currency_symbol = "₩"
        is_us_stock = False
        if "Close" in raw_data.columns:
            last_price = raw_data['Close'].iloc[-1]
            # 대략적인 가격으로 판별하거나 티커명으로 판별
            if input_ticker.endswith(".KS") or input_ticker.endswith(".KQ"):
                is_us_stock = False
            else:
                is_us_stock = True
                currency_symbol = "$"

        # 날짜 필터링
        end_date = raw_data.index.max()
        start_date = end_date - pd.DateOffset(years=years)
        df = raw_data[raw_data.index >= start_date].copy()
        
        # ---------------------------
        # 백테스팅 로직 (배당금 포함)
        # ---------------------------
        
        # 투자금 계산 (주기별)
        per_trade_krw = monthly_budget
        if interval_type == "매주": per_trade_krw = monthly_budget * 12 / 52
        elif interval_type == "매일": per_trade_krw = monthly_budget * 12 / 250
        
        # 실제 투입 통화로 변환 (해외주식이면 달러로 환전했다고 가정)
        per_trade_amt = per_trade_krw
        if is_us_stock:
            per_trade_amt = per_trade_krw / usd_krw

        total_shares = 0
        total_invested_currency = 0 # 해당 통화 기준
        cash_balance = 0 # 배당금 누적 (재투자 안 할 경우)
        
        history = [] # 차트용 데이터 저장
        cashflows = [] # XIRR용 [(date, -invest), ...]
        
        # 물가상승 시뮬레이션용 변수
        inflation_principal = 0 # 매회 투자금이 2%씩 자랐다면?
        daily_inf_rate = (1.02) ** (1/365) - 1 # 일일 물가상승분
        
        # 매수 시점 결정
        buy_indices = []
        if interval_type == "매일": buy_indices = df.index
        elif interval_type == "매월":
            buy_indices = df.groupby([df.index.year, df.index.month]).apply(lambda x: x.index[0]).tolist()
        elif interval_type == "매주":
            # 금요일 매수 가정
            buy_indices = df[df.index.dayofweek == 4].index

        prev_date = df.index[0]
        
        for date, row in df.iterrows():
            price = row['Close']
            
            # 1. 물가상승분 업데이트 (이전 날짜와의 차이만큼 성장)
            days_diff = (date - prev_date).days
            if inflation_principal > 0:
                inflation_principal *= (1.02) ** (days_diff / 365)
            prev_date = date

            # 2. 배당금 처리
            if use_dividend and row.get('Dividends', 0) > 0:
                div_amount = row['Dividends'] * total_shares
                # 재투자: 배당금으로 주식 즉시 매수
                if div_amount > 0:
                    added_shares = div_amount / price
                    total_shares += added_shares
            
            # 3. 정기 매수 처리
            if date in buy_indices:
                buy_qty = per_trade_amt / price
                total_shares += buy_qty
                total_invested_currency += per_trade_amt
                inflation_principal += per_trade_amt # 새 원금 추가
                
                # XIRR용 현금흐름 추가 (투자는 마이너스)
                # 원화 기준 수익률을 보기 위해 원화로 환산하여 기록
                invest_krw = per_trade_amt * (usd_krw if is_us_stock else 1)
                cashflows.append(-invest_krw)

            # 4. 일별 기록 저장
            cur_val_currency = total_shares * price
            
            # 원화 환산 기록 (차트용)
            rate = usd_krw if is_us_stock else 1
            
            history.append({
                "date": date,
                "invested": total_invested_currency * rate,
                "total_value": cur_val_currency * rate,
                "inflation_principal": inflation_principal * rate
            })
            
        # ---------------------------
        # 결과 계산
        # ---------------------------
        df_res = pd.DataFrame(history)
        
        final_invested_krw = df_res['invested'].iloc[-1]
        final_value_krw = df_res['total_value'].iloc[-1]
        final_inf_krw = df_res['inflation_principal'].iloc[-1]
        
        # 단순 수익률
        profit_rate = (final_value_krw - final_invested_krw) / final_invested_krw * 100
        
        # 초과 수익 (물가상승 대비)
        excess_return = final_value_krw - final_inf_krw
        
        # XIRR 계산
        # 마지막 날 평가액을 플러스 현금흐름으로 추가
        xirr_dates = [h['date'] for h in history if h['date'] in buy_indices] # 매수일만 추출 필요하지만 history 길이가 다름
        # 간단히: cashflows 리스트와 매칭되는 날짜 리스트를 다시 생성해야 함
        
        # XIRR 날짜 매핑 다시 정리
        xirr_input_dates = []
        xirr_input_flows = []
        # 매수 시점들
        for d in buy_indices:
            if d <= end_date:
                xirr_input_dates.append(d)
                xirr_input_flows.append(-per_trade_krw) # 매회 원화 투입액
        # 최종 평가일
        xirr_input_dates.append(df_res['date'].iloc[-1])
        xirr_input_flows.append(final_value_krw)
        
        try:
            xirr_val = xirr(xirr_input_flows, xirr_input_dates) * 100
        except:
            xirr_val = 0.0

        # ---------------------------
        # UI 출력
        # ---------------------------
        st.divider()
        st.subheader(f"📊 {input_ticker} ({input_query}) 분석 결과")

        # 단위 변환 라디오 버튼
        unit_opt = st.radio("금액 단위 선택", ["원", "만원", "백만원", "억원"], horizontal=True)

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("총 투자원금", format_currency(final_invested_krw, unit_opt))
        col2.metric("최종 평가액", format_currency(final_value_krw, unit_opt))
        col3.metric("단순 수익률 / XIRR", f"{profit_rate:.1f}% / {xirr_val:.1f}%")
        col4.metric("초과 수익 (vs 물가2%)", format_currency(excess_return, unit_opt), 
                    delta_color="normal" if excess_return > 0 else "inverse")

        # 차트 그리기 (확대축소 없는 이미지 형태)
        chart_buf = create_chart(df_res, input_query)
        st.image(chart_buf, use_container_width=True)

        # AI 분석
        ai_text = "AI 분석을 선택하지 않았습니다."
        if ai_use and GEMINI_API_KEY:
            with st.spinner("🤖 AI가 차트와 수익률을 분석 중입니다..."):
                prompt = f"""
                당신은 펀드매니저입니다. 다음 {input_query} 적립식 투자 결과를 분석해주세요.
                기간: {years}년
                투자방식: {interval_type} {monthly_budget}원
                배당재투자: {'함' if use_dividend else '안함'}
                
                성과:
                - 총원금: {final_invested_krw:,.0f}원
                - 최종액: {final_value_krw:,.0f}원
                - 수익률: {profit_rate:.2f}% (XIRR: {xirr_val:.2f}%)
                - 물가상승(2%) 대비 초과수익: {excess_return:,.0f}원
                
                1. 전략 평가 (DCA 유효성)
                2. 수익률 분석 (XIRR 관점)
                3. 향후 조언
                을 400자 이내로 요약해주세요.
                """
                try:
                    model = genai.GenerativeModel("gemini-pro")
                    ai_text = model.generate_content(prompt).text
                    st.info(ai_text)
                except Exception as e:
                    st.error(f"AI 분석 오류: {e}")

        # PDF 다운로드
        pdf_data = create_pdf(input_query, ai_text, profit_rate, xirr_val, 
                              final_invested_krw, final_value_krw, excess_return, chart_buf)
        st.download_button("📄 PDF 리포트 다운로드 (차트 포함)", pdf_data, f"{input_query}_report.pdf", "application/pdf")

# 실행 진입점
if __name__ == "__main__":
    # OAuth 등 토큰 체크 로직은 기존 코드 유지
    if "token" not in st.session_state and "user_email" not in st.session_state:
        # (기존 show_landing_page 호출)
        st.warning("로그인이 필요합니다 (기존 코드의 show_landing_page 사용)")
    else:
        show_main_app()
