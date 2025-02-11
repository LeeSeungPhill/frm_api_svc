from typing import List, Optional
import numpy as np
import matplotlib.pyplot as plt
import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

# 설정
days = 7  # 예측 기간
simulations = 10000  # 시뮬레이션 횟수



def get_current_kospi():
    url = "https://kr.investing.com/indices/kospi"
    
    # Selenium WebDriver 설정
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
    
    try:
        driver.get(url)
        kospi_value = driver.find_element(By.CSS_SELECTOR, "span[data-test='instrument-price-last']")
        return float(kospi_value.text.replace(",", ""))
    finally:
        driver.quit()

# 수익률 생성 함수
def change():
    rate = 0.0102605 * np.random.randn(1) + 0.0002302
    return rate[0]

def montecarlo() -> None:
    print("222")
    try:
        current_kospi = get_current_kospi()
        print(f"현재 KOSPI 지수: {current_kospi}")
    except Exception as e:
        print(f"오류 발생: {e}")

    # 몬테카를로 시뮬레이션
    final_values = []
    for _ in range(simulations):
        kospi = current_kospi
        for _ in range(days):
            kospi *= (1 + change())  # 매일의 수익률 적용
        final_values.append(kospi)

    # 결과 분석
    expected_value = np.mean(final_values)
    percentile_5 = np.percentile(final_values, 5)  # 하위 5%
    percentile_95 = np.percentile(final_values, 95)  # 상위 5%

    # 결과 출력
    print(f"1주일 후 예상 코스피 지수 (평균): {expected_value:.2f}")
    print(f"1주일 후 코스피 지수 90% 신뢰구간: [{percentile_5:.2f}, {percentile_95:.2f}]")

    # 시각화
    plt.hist(final_values, bins=50, color='blue', alpha=0.7)
    plt.title("Monte Carlo Simulation of KOSPI (1 Week)")
    plt.axvline(x=percentile_5, color='red', linestyle='--', label='5th Percentile')
    plt.axvline(x=percentile_95, color='green', linestyle='--', label='95th Percentile')
    plt.axvline(x=expected_value, color='orange', linestyle='-', label='Mean')
    plt.xlabel("KOSPI Value")
    plt.ylabel("Frequency")
    plt.legend()
    plt.show()
