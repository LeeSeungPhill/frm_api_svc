from config.db import SessionLocal 
from models.trade_mng import account_list as BasicRequest
from services import cust_mng_service
from routers.trade_mng import account_list, balance, place_order, get_order, bithumb_order, bithumb_get_order
from urllib.parse import urlencode, unquote
import hashlib
import uuid
import jwt
import requests
import time
from datetime import datetime
import os
from sqlalchemy import text
from decimal import Decimal, ROUND_HALF_UP, getcontext, ROUND_DOWN, InvalidOperation
from typing import Optional

upbit_api_url = os.getenv("UPBIT_API")
bithumb_api_url = os.getenv("BITHUMB_API")

user_id = "SLACK_TRADE"

def format_number(value):
    try:
        return f"{float(value):,.2f}" if isinstance(value, float) else f"{int(value):,}"
    except:
        return str(value)

def get_balance(cust_nm: str, market_name: str) -> str:
    db = SessionLocal()
    try:
        req_data = BasicRequest(cust_nm=cust_nm, market_name=market_name)
        result = account_list(req_data, db)

        text_lines = []
        for item in result["balance_list"]:
            
            if item['name'] == "P":
                text_lines.append(
                    f"*포인트*: {format_number(item['amt'])}"
                )
            elif item['name'] == "KRW":
                text_lines.append(
                    f"*보유현금*: {format_number(item['amt'])}원"
                )    
            else:    
                text_lines.append(
                    f"*{item['name']}*: {format_number(item['price']) if float(item['price']) > 0 else ''}{' ['+format_number(item['trade_price'])+']' if float(item['trade_price']) > 0 else ''}\n"
                    f"> 보유량: {format_number(item['volume'])}{' ('+format_number(item['locked_volume'])+')' if float(item['locked_volume']) > 0  else ''}\n"
                    f"> 원금액: {format_number(item['amt'])}원, 평가액: {format_number(item['current_amt'])}원\n"
                    f"> 손익금: {format_number(item['loss_profit_amt'])}원, 손익률: {item['loss_profit_rate']}%"
                )

        return "\n".join(text_lines) if text_lines else "잔고가 없습니다."
    except Exception as e:
        return f"잔고조회 실패: {e}"
    finally:
        db.close()

def buy_proc(cust_nm: str, market_name: str, gubun: str, prd_nm: str, price: Optional[float] = None, cut_price: Optional[float] = None, custom_volumn: Optional[float] = None, buy_amt: Optional[float] = None, cut_amt: Optional[float] = None,) -> str:
    db = SessionLocal()
    try:
        text_lines = []
              
        # 고객명에 의한 고객정보 조회
        cust_info = cust_mng_service.get_cust_info_by_cust_nm(db, cust_nm, market_name)

        # access key
        access_key = cust_info[4]
        # secret_key
        secret_key = cust_info[5]

        # 잔고정보 조회
        balance_info = balance(access_key, secret_key, market_name)
        hold_price = 0
        hold_vol = 0
        
        for item in balance_info:
            if prd_nm == item["name"]:
                hold_price = float(item['price'])                                   # 매수평균가    
                hold_vol = float(item['volume']) + float(item['locked_volume'])     # 보유수량 = 주문가능 수량 + 주문묶여있는 수량        

        if gubun ==  "cut":
            
            price = float(price)
            volume = float(cut_amt) / (price - float(cut_price))
            ord_amt = int(Decimal(str(price)) * Decimal(str(volume)))
        
        elif gubun ==  "amt":
            
            price = float(price)
            volume = (Decimal(str(buy_amt)) / Decimal(str(price))).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)
            ord_amt = int(Decimal(str(price)) * Decimal(str(volume)))
            
        elif gubun ==  "direct":
            
            price = 0
            volume = 0
            ord_amt = 0

            params = {
                "markets": "KRW-"+ prd_nm
            }
            
            try:
                
                if market_name == 'UPBIT':                    
                    # 현재가 정보
                    res = requests.get(upbit_api_url + "/v1/ticker", params=params).json()
                elif market_name == 'BITHUMB': 
                    # 현재가 정보
                    res = requests.get(bithumb_api_url + "/v1/ticker", params=params).json()   

                if isinstance(res, dict) and 'error' in res:
                    # 에러 메시지가 반환된 경우
                    error_name = res['error'].get('name', 'Unknown')
                    error_message = res['error'].get('message', 'Unknown')
                    print(f"[Ticker 조회 오류] {error_name}: {error_message}")

            except Exception as e:
                print(f"[Ticker 조회 예외] 오류 발생: {e}")
                res = None
            
            if len(res) > 0:                
                price = float(res[0]['trade_price'])                 
                volume = (Decimal(buy_amt) / Decimal(price)).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)
                ord_amt = int(Decimal(str(price)) * Decimal(str(volume))) 
                  
        elif gubun ==  "custom":
            
            price = float(price)  
            volume = float(custom_volumn)      
            ord_amt = int(Decimal(str(price)) * Decimal(str(custom_volumn)))
                        
        # 주문유형 설정 : 시장가 매수 주문, 지정가 주문
        ord_type = "price" if gubun == "direct" else "limit"

        if market_name == 'UPBIT':
            
            try:
                payload = {
                    'access_key': access_key,
                    'nonce': str(uuid.uuid4()),
                }

                jwt_token = jwt.encode(payload, secret_key)
                authorization = 'Bearer {}'.format(jwt_token)
                headers = {
                    'Authorization': authorization,
                }

                # 잔고 조회
                accounts = requests.get(upbit_api_url + '/v1/accounts', headers=headers).json()

            except Exception as e:
                print(f"[잔고 조회 예외] 오류 발생: {e}")
                accounts = []  # 또는 None 등, 이후 구문에서 사용할 수 있도록 기본값 설정
            
            trade_cash = 0
            
            for item in accounts:
                if "KRW" == item['currency']:  
                    if Decimal(item['balance']) == 0:
                        trade_cash = Decimal('0')
                    else:
                        getcontext().prec = 28  # 정밀도 설정 (기본은 28자리)

                        try:
                            balance_str = str(item['balance'])  # Decimal은 문자열로 받는 것이 가장 안전
                            balance_decimal = Decimal(balance_str)
                            # 수수료를 제외한 주문가능 금액
                            trade_cash = (balance_decimal * Decimal('0.9995')).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)
                        except InvalidOperation as e:
                            print(f"잘못된 Decimal 연산: {item['balance']} → {e}")
            
            # 주문금액보다 주문가능 금액이 더 큰 경우
            if int(trade_cash) >= ord_amt:
                
                order_response = place_order(
                    access_key, 
                    secret_key,
                    market="KRW-"+prd_nm,
                    side="bid",                     # 매수
                    volume=str(volume),             # 매수량
                    price=str(ord_amt) if ord_type == "price" else str(price),               # 시장가 : 매수금액, 지정가 : 매수가격
                    ord_type=ord_type               # 주문유형
                )

                print("주문 응답:", order_response)

                if "uuid" in order_response:
                    ord_no  = order_response["uuid"]  # 주문 ID
                    time.sleep(1)

                    order_status = get_order(access_key, secret_key, ord_no)
                    print("주문 상태:", order_status)
                    # 주문관리정보 생성
                    INSERT_TRADE_INFO = """
                        INSERT INTO trade_mng (
                            cust_num, 
                            market_name, 
                            ord_dtm, 
                            ord_no, 
                            prd_nm, 
                            ord_tp,
                            ord_state,
                            ord_count,
                            ord_expect_totamt,
                            ord_price,
                            ord_vol,
                            ord_amt,
                            cut_price,
                            cut_rate,
                            cut_amt,
                            goal_price,
                            goal_rate,
                            goal_amt,
                            margin_vol,
                            executed_vol,
                            remaining_vol,
                            hold_price,
                            hold_vol,
                            paid_fee,
                            ord_type,
                            regr_id, 
                            reg_date, 
                            chgr_id, 
                            chg_date)
                        VALUES (
                            :cust_num, 
                            :market_name, 
                            :ord_dtm,
                            :ord_no,
                            :prd_nm,
                            :ord_tp,
                            :ord_state,
                            :ord_count,
                            :ord_expect_totamt,
                            :ord_price,
                            :ord_vol,
                            :ord_amt,
                            :cut_price,
                            :cut_rate,
                            :cut_amt,
                            :goal_price,
                            :goal_rate,
                            :goal_amt,
                            :margin_vol,
                            :executed_vol,
                            :remaining_vol,
                            :hold_price,
                            :hold_vol,
                            :paid_fee,
                            :ord_type,
                            :regr_id,
                            :reg_date,
                            :chgr_id,
                            :chg_date)
                    """
                    db.execute(text(INSERT_TRADE_INFO), {
                        "cust_num": cust_info[0], 
                        "market_name": market_name, 
                        "ord_dtm": datetime.fromisoformat(order_status['created_at']).strftime("%Y%m%d%H%M%S"), 
                        "ord_no": ord_no, 
                        "prd_nm": "KRW-"+prd_nm,
                        "ord_tp": "01",
                        "ord_state": "done" if ord_type == "price" else order_status['state'],
                        "ord_count": 0,
                        "ord_expect_totamt": 0,
                        "ord_price": Decimal(order_status['price']) if order_status['trades_count'] ==  0 else sum(Decimal(trade['funds']) for trade in order_status['trades']) / sum(Decimal(trade['volume']) for trade in order_status['trades']),
                        "ord_vol": Decimal(order_status['volume']) if order_status['trades_count'] ==  0 else sum(Decimal(trade['volume']) for trade in order_status['trades']),
                        "ord_amt": int(Decimal(order_status['price'])*Decimal(order_status['volume'])) if order_status['trades_count'] ==  0 else int(sum(Decimal(trade['funds']) for trade in order_status['trades'])),
                        "cut_price": 0,
                        "cut_rate": 0,
                        "cut_amt": 0,
                        "goal_price": 0,
                        "goal_rate": 0,
                        "goal_amt": 0,
                        "margin_vol": 0,
                        "executed_vol": Decimal(order_status['executed_volume']),
                        "remaining_vol": Decimal(order_status['remaining_volume']) if order_status['trades_count'] ==  0 else 0,
                        "hold_price":hold_price,
                        "hold_vol":hold_vol,
                        "paid_fee": Decimal(order_status['paid_fee']),
                        "ord_type":ord_type,
                        "regr_id": user_id,
                        "reg_date": datetime.now(),
                        "chgr_id": user_id,
                        "chg_date": datetime.now()
                        })
                    db.commit()

                    summary = (
                            f"*{prd_nm}*: {'매수' if order_status['side'] == 'bid' else '매도'} 주문 {'done' if ord_type == 'price' else order_status['state']} 상태\n"
                            f"> 주문단가: {format_number(order_status['price']) if order_status['trades_count'] ==  0 else format_number(sum(Decimal(trade['funds']) for trade in order_status['trades']) / sum(Decimal(trade['volume']) for trade in order_status['trades']))}\n"
                            f"> 주문시간: {datetime.fromisoformat(order_status['created_at']).strftime('%Y-%m-%d %H:%M:%S')}\n"
                            f"> 주문량: {format_number(order_status['volume']) if order_status['trades_count'] ==  0 else sum(Decimal(trade['volume']) for trade in order_status['trades'])}, 채결량: {format_number(order_status['executed_volume'])}, 잔량: {format_number(order_status['remaining_volume'] if order_status['trades_count'] ==  0 else 0)}"
                    )
                    text_lines.append((summary, order_status['uuid']))
                else:
                    fail_text = f"*{prd_nm} : 매수 주문 실패했습니다.* => {order_response['error']['message']}"
                    text_lines.append({"text": fail_text, "order_no": ""})        
            
            else:
                fail_text = f"*{prd_nm} : 매수 가능 현금이 부족합니다.*"
                text_lines.append({"text": fail_text, "order_no": ""})                           
        
        elif market_name == 'BITHUMB':
            
            try:
                payload = {
                    'access_key': access_key,
                    'nonce': str(uuid.uuid4()),
                    'timestamp': round(time.time() * 1000)
                }

                jwt_token = jwt.encode(payload, secret_key)
                authorization = 'Bearer {}'.format(jwt_token)
                headers = {
                    'Authorization': authorization,
                }

                # 잔고 조회
                accounts = requests.get(bithumb_api_url + '/v1/accounts', headers=headers).json()

            except Exception as e:
                print(f"[잔고 조회 예외] 오류 발생: {e}")
                accounts = []  # 또는 None 등, 이후 구문에서 사용할 수 있도록 기본값 설정
            
            trade_cash = 0
            
            for item in accounts:
                if "KRW" == item['currency']:
                    if Decimal(item['balance']) == 0:
                        trade_cash = Decimal('0')
                    else:
                        getcontext().prec = 28  # 정밀도 설정 (기본은 28자리)

                        try:
                            balance_str = str(item['balance'])  # Decimal은 문자열로 받는 것이 가장 안전
                            balance_decimal = Decimal(balance_str)
                            # 수수료를 제외한 주문가능 금액
                            trade_cash = (balance_decimal * Decimal('0.9995')).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)
                        except InvalidOperation as e:
                            print(f"잘못된 Decimal 연산: {item['balance']} → {e}")
            
            # 주문금액보다 주문가능 금액이 더 큰 경우
            if int(trade_cash) >= ord_amt:
            
                order_response = bithumb_order(
                    access_key, 
                    secret_key,
                    market="KRW-"+prd_nm,
                    side="bid",                     # 매수
                    volume=str(volume),             # 매수량
                    price=str(ord_amt) if ord_type == "price" else str(price),               # 시장가 : 매수금액, 지정가 : 매수가격
                    ord_type=ord_type               # 주문유형
                )
            
                print("주문 응답:", order_response)

                if "uuid" in order_response:
                    ord_no  = order_response["uuid"]  # 주문 ID
                    time.sleep(1)
                    order_status = bithumb_get_order(access_key, secret_key, ord_no)
                    print("주문 상태:", order_status)

                    # 주문관리정보 생성
                    INSERT_TRADE_INFO = """
                        INSERT INTO trade_mng (
                            cust_num, 
                            market_name, 
                            ord_dtm, 
                            ord_no, 
                            prd_nm, 
                            ord_tp,
                            ord_state,
                            ord_count,
                            ord_expect_totamt,
                            ord_price,
                            ord_vol,
                            ord_amt,
                            cut_price,
                            cut_rate,
                            cut_amt,
                            goal_price,
                            goal_rate,
                            goal_amt,
                            margin_vol,
                            executed_vol,
                            remaining_vol,
                            hold_price,
                            hold_vol,
                            paid_fee,
                            ord_type,
                            regr_id, 
                            reg_date, 
                            chgr_id, 
                            chg_date)
                        VALUES (
                            :cust_num, 
                            :market_name, 
                            :ord_dtm,
                            :ord_no,
                            :prd_nm,
                            :ord_tp,
                            :ord_state,
                            :ord_count,
                            :ord_expect_totamt,
                            :ord_price,
                            :ord_vol,
                            :ord_amt,
                            :cut_price,
                            :cut_rate,
                            :cut_amt,
                            :goal_price,
                            :goal_rate,
                            :goal_amt,
                            :margin_vol,
                            :executed_vol,
                            :remaining_vol,
                            :hold_price,
                            :hold_vol,
                            :paid_fee,
                            :ord_type,
                            :regr_id,
                            :reg_date,
                            :chgr_id,
                            :chg_date)
                    """
                    db.execute(text(INSERT_TRADE_INFO), {
                        "cust_num": cust_info[0], 
                        "market_name": market_name, 
                        "ord_dtm": datetime.fromisoformat(order_status['created_at']).strftime("%Y%m%d%H%M%S"), 
                        "ord_no": ord_no, 
                        "prd_nm": "KRW-"+prd_nm,
                        "ord_tp": "01",
                        "ord_state": "done" if ord_type == "price" else order_status['state'],
                        "ord_count": 0,
                        "ord_expect_totamt": 0,
                        "ord_price": Decimal(order_status['price']) if order_status['trades_count'] ==  0 else sum(Decimal(trade['funds']) for trade in order_status['trades']) / sum(Decimal(trade['volume']) for trade in order_status['trades']),
                        "ord_vol": Decimal(order_status['volume']) if order_status['trades_count'] ==  0 else sum(Decimal(trade['volume']) for trade in order_status['trades']),
                        "ord_amt": int(Decimal(order_status['price'])*Decimal(order_status['volume'])) if order_status['trades_count'] ==  0 else int(sum(Decimal(trade['funds']) for trade in order_status['trades'])),
                        "cut_price": 0,
                        "cut_rate": 0,
                        "cut_amt": 0,
                        "goal_price": 0,
                        "goal_rate": 0,
                        "goal_amt": 0,
                        "margin_vol": 0,
                        "executed_vol": Decimal(order_status['executed_volume']),
                        "remaining_vol": Decimal(order_status['remaining_volume']) if order_status['trades_count'] ==  0 else 0,
                        "hold_price":hold_price,
                        "hold_vol":hold_vol,
                        "paid_fee": Decimal(order_status['paid_fee']),
                        "ord_type":ord_type,
                        "regr_id": user_id,
                        "reg_date": datetime.now(),
                        "chgr_id": user_id,
                        "chg_date": datetime.now()
                        })
                    db.commit()
                    
                    summary = (
                            f"*{prd_nm}*: {'매수' if order_status['side'] == 'bid' else '매도'} 주문 {'done' if ord_type == 'price' else order_status['state']} 상태\n"
                            f"> 주문단가: {format_number(order_status['price']) if order_status['trades_count'] ==  0 else format_number(sum(Decimal(trade['funds']) for trade in order_status['trades']) / sum(Decimal(trade['volume']) for trade in order_status['trades']))}\n"
                            f"> 주문시간: {datetime.fromisoformat(order_status['created_at']).strftime('%Y-%m-%d %H:%M:%S')}\n"
                            f"> 주문량: {format_number(order_status['volume']) if order_status['trades_count'] ==  0 else sum(Decimal(trade['volume']) for trade in order_status['trades'])}, 채결량: {format_number(order_status['executed_volume'])}, 잔량: {format_number(order_status['remaining_volume'] if order_status['trades_count'] ==  0 else 0)}"
                    )
                    text_lines.append((summary, order_status['uuid']))
                else:
                    fail_text = f"*{prd_nm} : 매수 주문 실패했습니다.* => {order_response['error']['message']}"
                    text_lines.append({"text": fail_text, "order_no": ""})                   
                    
            else:
                fail_text = f"*{prd_nm} : 매수 가능 현금이 부족합니다.*"
                text_lines.append({"text": fail_text, "order_no": ""})                           

        return text_lines if text_lines else text_lines.append(('', ''))
    except Exception as e:
        return f"매수주문 실패: {e}"
    finally:
        db.close()

def sell_proc(cust_nm: str, market_name: str, gubun: str, prd_nm: str, price: Optional[float] = None, custom_volumn_rate: Optional[float] = None, custom_volumn: Optional[float] = None,) -> str:
    db = SessionLocal()
    try:
        text_lines = []
              
        # 고객명에 의한 고객정보 조회
        cust_info = cust_mng_service.get_cust_info_by_cust_nm(db, cust_nm, market_name)

        # access key
        access_key = cust_info[4]
        # secret_key
        secret_key = cust_info[5]

        # 잔고조회
        raw_balance_list = balance(access_key, secret_key, market_name, prd_nm)

        # 잔고조회의 매수평균가, 보유수량 가져오기                     
        hold_price = 0
        hold_vol = 0
        volume = 0
        if len(raw_balance_list) > 0:   
            for item in raw_balance_list: 
                
                hold_price = float(item['price'])                                   # 매수평균가    
                hold_vol = float(item['volume']) + float(item['locked_volume'])     # 보유수량 = 주문가능 수량 + 주문묶여있는 수량
                
                # 매도 가능 수량 설정 : volumn - locked_volume
                available_volume = Decimal(str(item['volume'])) - Decimal(str(item['locked_volume']))

                getcontext().prec = 10 
                
                if gubun == "all":
                    volume = float(available_volume)
                elif gubun == "half":
                    volume = float((available_volume / Decimal("2")).quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP))
                elif gubun in ["66", "33", "25", "20"]:
                    ratio = Decimal(gubun) / Decimal("100")
                    volume = float((available_volume * ratio).quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP))    
                elif gubun == "direct":
                    ratio = Decimal(custom_volumn_rate) / Decimal("100")
                    volume = float((available_volume * ratio).quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP)) 
                elif gubun == "custom":  
                    # 사용자 매도량과 매도 가능 수량 비교
                    if float(custom_volumn) <= (float(item['volume']) - float(item['locked_volume'])): 
                        volume = float(custom_volumn)
                else:
                    # gubun이 지정되지 않은 경우 예외 처리 또는 기본값 처리
                    volume = 0        
                
            if volume > 0: # 매도물량 존재하는 경우
                print("order available volume : ",volume)
                
                # 주문유형 설정 : 시장가 매도 주문, 지정가 주문
                ord_type = "market" if gubun == "direct" else "limit"

                if market_name == 'UPBIT':
                    order_response = place_order(
                        access_key, 
                        secret_key,
                        market="KRW-"+prd_nm,
                        side="ask",                     # 매도
                        volume=str(volume),             # 매도량
                        price=str(price),               # 매도가격
                        ord_type=ord_type               # 주문유형
                    )

                    print("주문 응답:", order_response)

                    if "uuid" in order_response:
                        ord_no  = order_response["uuid"]  # 주문 ID
                        time.sleep(1)
                        order_status = get_order(access_key, secret_key, ord_no)
                        print("주문 상태:", order_status)

                        # 주문관리정보 생성
                        INSERT_TRADE_INFO = """
                            INSERT INTO trade_mng (
                                cust_num, 
                                market_name, 
                                ord_dtm, 
                                ord_no, 
                                prd_nm, 
                                ord_tp,
                                ord_state,
                                ord_count,
                                ord_expect_totamt,
                                ord_price,
                                ord_vol,
                                ord_amt,
                                cut_price,
                                cut_rate,
                                cut_amt,
                                goal_price,
                                goal_rate,
                                goal_amt,
                                margin_vol,
                                executed_vol,
                                remaining_vol,
                                hold_price,
	                            hold_vol,
                                paid_fee,
                                ord_type,
                                regr_id, 
                                reg_date, 
                                chgr_id, 
                                chg_date)
                            VALUES (
                                :cust_num, 
                                :market_name, 
                                :ord_dtm,
                                :ord_no,
                                :prd_nm,
                                :ord_tp,
                                :ord_state,
                                :ord_count,
                                :ord_expect_totamt,
                                :ord_price,
                                :ord_vol,
                                :ord_amt,
                                :cut_price,
                                :cut_rate,
                                :cut_amt,
                                :goal_price,
                                :goal_rate,
                                :goal_amt,
                                :margin_vol,
                                :executed_vol,
                                :remaining_vol,
                                :hold_price,
	                            :hold_vol,
                                :paid_fee,
                                :ord_type,
                                :regr_id,
                                :reg_date,
                                :chgr_id,
                                :chg_date)
                        """
                        db.execute(text(INSERT_TRADE_INFO), {
                            "cust_num": cust_info[0], 
                            "market_name": market_name, 
                            "ord_dtm": datetime.fromisoformat(order_status['created_at']).strftime("%Y%m%d%H%M%S"), 
                            "ord_no": ord_no, 
                            "prd_nm": "KRW-"+prd_nm,
                            "ord_tp": "02",
                            "ord_state": order_status['state'],
                            "ord_count": 0,
                            "ord_expect_totamt": 0,
                            "ord_price": Decimal(order_status['price']) if order_status['trades_count'] ==  0 else sum(Decimal(trade['funds']) for trade in order_status['trades']) / sum(Decimal(trade['volume']) for trade in order_status['trades']),
                            "ord_vol": Decimal(order_status['volume']) if order_status['trades_count'] ==  0 else sum(Decimal(trade['volume']) for trade in order_status['trades']),
                            "ord_amt": int(Decimal(order_status['price'])*Decimal(order_status['volume'])) if order_status['trades_count'] ==  0 else int(sum(Decimal(trade['funds']) for trade in order_status['trades'])),
                            "cut_price": 0,
                            "cut_rate": 0,
                            "cut_amt": 0,
                            "goal_price": 0,
                            "goal_rate": 0,
                            "goal_amt": 0,
                            "margin_vol": 0,
                            "executed_vol": Decimal(order_status['executed_volume']),
                            "remaining_vol": Decimal(order_status['remaining_volume']),
                            "hold_price":hold_price,
	                        "hold_vol":hold_vol,
                            "paid_fee": Decimal(order_status['paid_fee']),
                            "ord_type":ord_type,
                            "regr_id": user_id,
                            "reg_date": datetime.now(),
                            "chgr_id": user_id,
                            "chg_date": datetime.now()
                            })
                        db.commit()

                        summary = (
                                f"*{prd_nm}*: {'매수' if order_status['side'] == 'bid' else '매도'} 주문 {order_status['state']} 상태\n"
                                f"> 주문단가: {format_number(order_status['price']) if order_status['trades_count'] ==  0 else format_number(sum(Decimal(trade['funds']) for trade in order_status['trades']) / sum(Decimal(trade['volume']) for trade in order_status['trades']))}\n"
                                f"> 주문시간: {datetime.fromisoformat(order_status['created_at']).strftime('%Y-%m-%d %H:%M:%S')}\n"
                                f"> 주문량: {format_number(order_status['volume'])}, 채결량: {format_number(order_status['executed_volume'])}, 잔량: {format_number(order_status['remaining_volume'])}"
                        )
                        text_lines.append((summary, order_status['uuid']))
                    else:
                        fail_text = f"*{prd_nm} : 매도 주문 실패했습니다.* => {order_response['error']['message']}"
                        text_lines.append({"text": fail_text, "order_no": ""})

                elif market_name == 'BITHUMB':
                    order_response = bithumb_order(
                        access_key, 
                        secret_key,
                        market="KRW-"+prd_nm,
                        side="ask",                     # 매도
                        volume=str(volume),             # 매도량
                        price=str(price),               # 매도가격
                        ord_type=ord_type               # 주문유형
                    )
                
                    print("주문 응답:", order_response)

                    if "uuid" in order_response:
                        ord_no  = order_response["uuid"]  # 주문 ID
                        time.sleep(1)
                        order_status = bithumb_get_order(access_key, secret_key, ord_no)
                        print("주문 상태:", order_status)

                        # 주문관리정보 생성
                        INSERT_TRADE_INFO = """
                            INSERT INTO trade_mng (
                                cust_num, 
                                market_name, 
                                ord_dtm, 
                                ord_no, 
                                prd_nm, 
                                ord_tp,
                                ord_state,
                                ord_count,
                                ord_expect_totamt,
                                ord_price,
                                ord_vol,
                                ord_amt,
                                cut_price,
                                cut_rate,
                                cut_amt,
                                goal_price,
                                goal_rate,
                                goal_amt,
                                margin_vol,
                                executed_vol,
                                remaining_vol,
                                hold_price,
                                hold_vol,
                                paid_fee,
                                ord_type,
                                regr_id, 
                                reg_date, 
                                chgr_id, 
                                chg_date)
                            VALUES (
                                :cust_num, 
                                :market_name, 
                                :ord_dtm,
                                :ord_no,
                                :prd_nm,
                                :ord_tp,
                                :ord_state,
                                :ord_count,
                                :ord_expect_totamt,
                                :ord_price,
                                :ord_vol,
                                :ord_amt,
                                :cut_price,
                                :cut_rate,
                                :cut_amt,
                                :goal_price,
                                :goal_rate,
                                :goal_amt,
                                :margin_vol,
                                :executed_vol,
                                :remaining_vol,
                                :hold_price,
                                :hold_vol,
                                :paid_fee,
                                :ord_type,
                                :regr_id,
                                :reg_date,
                                :chgr_id,
                                :chg_date)
                        """
                        db.execute(text(INSERT_TRADE_INFO), {
                            "cust_num": cust_info[0], 
                            "market_name": market_name, 
                            "ord_dtm": datetime.fromisoformat(order_status['created_at']).strftime("%Y%m%d%H%M%S"), 
                            "ord_no": ord_no, 
                            "prd_nm": "KRW-"+prd_nm,
                            "ord_tp": "02",
                            "ord_state": order_status['state'],
                            "ord_count": 0,
                            "ord_expect_totamt": 0,
                            "ord_price": Decimal(order_status['price']) if order_status['trades_count'] ==  0 else sum(Decimal(trade['funds']) for trade in order_status['trades']) / sum(Decimal(trade['volume']) for trade in order_status['trades']),
                            "ord_vol": Decimal(order_status['volume']) if order_status['trades_count'] ==  0 else sum(Decimal(trade['volume']) for trade in order_status['trades']),
                            "ord_amt": int(Decimal(order_status['price'])*Decimal(order_status['volume'])) if order_status['trades_count'] ==  0 else int(sum(Decimal(trade['funds']) for trade in order_status['trades'])),
                            "cut_price": 0,
                            "cut_rate": 0,
                            "cut_amt": 0,
                            "goal_price": 0,
                            "goal_rate": 0,
                            "goal_amt": 0,
                            "margin_vol": 0,
                            "executed_vol": Decimal(order_status['executed_volume']),
                            "remaining_vol": Decimal(order_status['remaining_volume']),
                            "hold_price":hold_price,
	                        "hold_vol":hold_vol,
                            "paid_fee": Decimal(order_status['paid_fee']),
                            "ord_type":ord_type,
                            "regr_id": user_id,
                            "reg_date": datetime.now(),
                            "chgr_id": user_id,
                            "chg_date": datetime.now()
                            })
                        db.commit()
                        
                        summary = (
                                f"*{prd_nm}*: {'매수' if order_status['side'] == 'bid' else '매도'} 주문 {order_status['state']} 상태\n"
                                f"> 주문단가: {format_number(order_status['price']) if order_status['trades_count'] ==  0 else format_number(sum(Decimal(trade['funds']) for trade in order_status['trades']) / sum(Decimal(trade['volume']) for trade in order_status['trades']))}\n"
                                f"> 주문시간: {datetime.fromisoformat(order_status['created_at']).strftime('%Y-%m-%d %H:%M:%S')}\n"
                                f"> 주문량: {format_number(order_status['volume'])}, 채결량: {format_number(order_status['executed_volume'])}, 잔량: {format_number(order_status['remaining_volume'])}"
                        )
                        text_lines.append((summary, order_status['uuid']))
                    else:
                        fail_text = f"*{prd_nm} : 매도 주문 실패했습니다.* => {order_response['error']['message']}"
                        text_lines.append({"text": fail_text, "order_no": ""})
                    
            else:
                fail_text = f"*{prd_nm} : 매도 가능 수량 부족합니다.*"
                text_lines.append({"text": fail_text, "order_no": ""})
        
        else:
            fail_text = f"*{prd_nm} : 매도 가능 상품이 미존재합니다.*"
            text_lines.append({"text": fail_text, "order_no": ""})                        

        return text_lines if text_lines else text_lines.append(('', ''))
    except Exception as e:
        return f"매도주문 실패: {e}"
    finally:
        db.close()

def get_order_open(cust_nm: str, market_name: str) -> str:
    db = SessionLocal()
    try:
        # 고객명에 의한 고객정보 조회
        cust_info = cust_mng_service.get_cust_info_by_cust_nm(db, cust_nm, market_name)

        # access key
        access_key = cust_info[4]
        # secret_key
        secret_key = cust_info[5]
        
        text_lines = []

        if market_name == 'UPBIT':
            
            # 업비트에서 거래 가능한 종목 목록
            url = "https://api.upbit.com/v1/market/all?is_details=false"
            headers = {"accept": "application/json"}
            market_list = requests.get(url, headers=headers).json()
            
            for item in market_list:
                params = {
                    'market': item['market'],
                    'states[]': ['wait', 'watch']
                }
                query_string = unquote(urlencode(params, doseq=True)).encode("utf-8")

                m = hashlib.sha512()
                m.update(query_string)
                query_hash = m.hexdigest()

                payload = {
                    'access_key': access_key,
                    'nonce': str(uuid.uuid4()),
                    'query_hash': query_hash,
                    'query_hash_alg': 'SHA512',
                }

                jwt_token = jwt.encode(payload, secret_key)
                authorization = 'Bearer {}'.format(jwt_token)
                headers = {
                    'Authorization': authorization,
                }

                # 체결 대기 주문 조회
                raw_order_list = requests.get(upbit_api_url + "/v1/orders/open", params=params, headers=headers).json()             

                for ord_info in raw_order_list:
                    
                    # 매매관리정보 존재여부 조회
                    SELECT_TRADE_MNG = """
                        SELECT 
                            ord_no
                        FROM trade_mng 
                        WHERE market_name = 'UPBIT'
                        AND cust_num = :cust_num
                        AND prd_nm = :prd_nm
                        AND (ord_no = :ord_no OR orgn_ord_no = :ord_no)

                        UNION ALL

                        SELECT 
                            ord_no
                        FROM trade_mng_hist
                        WHERE market_name = 'UPBIT'
                        AND cust_num = :cust_num
                        AND prd_nm = :prd_nm
                        AND (ord_no = :ord_no OR orgn_ord_no = :ord_no)
                    """
                    chk_trade_mng_list = db.execute(text(SELECT_TRADE_MNG), {"cust_num": cust_info[0], "prd_nm": item['market'], "ord_no": ord_info['uuid'],}).mappings().all()
                    
                    if len(chk_trade_mng_list) < 1:                  
                        # 매매관리정보 미존재 대상 생성 처리                    
                        INSERT_TRADE_INFO = """
                            INSERT INTO trade_mng (
                                cust_num, 
                                market_name, 
                                ord_dtm, 
                                ord_no, 
                                prd_nm, 
                                ord_tp,
                                ord_state,
                                ord_count,
                                ord_expect_totamt,
                                ord_price,
                                ord_vol,
                                ord_amt,
                                cut_price,
                                cut_rate,
                                cut_amt,
                                goal_price,
                                goal_rate,
                                goal_amt,
                                margin_vol,
                                executed_vol,
                                remaining_vol,
                                paid_fee,
                                ord_type,
                                regr_id, 
                                reg_date, 
                                chgr_id, 
                                chg_date)
                            VALUES (
                                :cust_num, 
                                :market_name, 
                                :ord_dtm,
                                :ord_no,
                                :prd_nm,
                                :ord_tp,
                                :ord_state,
                                :ord_count,
                                :ord_expect_totamt,
                                :ord_price,
                                :ord_vol,
                                :ord_amt,
                                :cut_price,
                                :cut_rate,
                                :cut_amt,
                                :goal_price,
                                :goal_rate,
                                :goal_amt,
                                :margin_vol,
                                :executed_vol,
                                :remaining_vol,
                                :paid_fee,
                                :ord_type,
                                :regr_id,
                                :reg_date,
                                :chgr_id,
                                :chg_date)
                        """
                        db.execute(text(INSERT_TRADE_INFO), {
                            "cust_num": cust_info[0], 
                            "market_name": market_name, 
                            "ord_dtm": datetime.fromisoformat(ord_info['created_at']).strftime("%Y%m%d%H%M%S"), 
                            "ord_no": ord_info['uuid'], 
                            "prd_nm": item['market'],
                            "ord_tp": "01" if ord_info['side'] == 'bid' else "02",
                            "ord_state": ord_info['state'],
                            "ord_count": 0,
                            "ord_expect_totamt": 0,
                            "ord_price": Decimal(ord_info['price']),
                            "ord_vol": Decimal(ord_info['remaining_volume']),
                            "ord_amt": int(Decimal(ord_info['price']) * Decimal(ord_info['remaining_volume'])),
                            "cut_price": 0,
                            "cut_rate": 0,
                            "cut_amt": 0,
                            "goal_price": 0,
                            "goal_rate": 0,
                            "goal_amt": 0,
                            "margin_vol": 0,
                            "executed_vol": Decimal(ord_info['executed_volume']),
                            "remaining_vol": Decimal(ord_info['remaining_volume']),
                            "paid_fee": Decimal(ord_info['paid_fee']),
                            "ord_type":ord_info['ord_type'],
                            "regr_id": user_id,
                            "reg_date": datetime.now(),
                            "chgr_id": user_id,
                            "chg_date": datetime.now()
                            })
                        db.commit()
                    
                    summary = (
                        f"*{item['market'].split('-')[-1]}*: {'매수' if ord_info['side'] == 'bid' else '매도'} 주문 {ord_info['state']} 상태\n"
                        f"> 주문단가: {format_number(ord_info['price'])}\n"
                        f"> 주문시간: {datetime.fromisoformat(ord_info['created_at']).strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"> 주문량: {format_number(ord_info['volume'])}, 채결량: {format_number(ord_info['executed_volume'])}, 잔량: {format_number(ord_info['remaining_volume'])}"
                    )
                    text_lines.append((summary, ord_info['uuid']))
            
        elif market_name == 'BITHUMB':
            
            # 대기 상태 주문관리정보 조회
            SELECT_OPEN_ORDER_INFO = """
                SELECT 
                    B.id, split_part(B.prd_nm, '-', 2) AS prd_nm, B.ord_state, B.executed_vol, B.remaining_vol, B.ord_no
                FROM cust_mng A LEFT OUTER JOIN trade_mng B 
                ON A.cust_num = B.cust_num AND A.market_name = B.market_name
                WHERE A.cust_nm = :cust_nm 
                AND A.market_name = :market_name 
                AND B.ord_state IN ('wait' ,'watch')
            """
            chk_ord_list = db.execute(text(SELECT_OPEN_ORDER_INFO), {"cust_nm": cust_nm, "market_name": market_name,}).mappings().all()
            
            for chk_ord in chk_ord_list :
                param = dict( uuid=chk_ord['ord_no'] )

                # Generate access token
                query = urlencode(param).encode()
                hash = hashlib.sha512()
                hash.update(query)
                query_hash = hash.hexdigest()
                payload = {
                    'access_key': access_key,
                    'nonce': str(uuid.uuid4()),
                    'timestamp': round(time.time() * 1000), 
                    'query_hash': query_hash,
                    'query_hash_alg': 'SHA512',
                }   
                jwt_token = jwt.encode(payload, secret_key)
                authorization_token = 'Bearer {}'.format(jwt_token)
                headers = {
                    'Authorization': authorization_token
                }
                # 개별 주문 조회
                response = requests.get(bithumb_api_url + '/v1/order', params=param, headers=headers).json()

                if response is not None:
                    
                    summary = (
                        f"*{chk_ord['prd_nm']}*: {'매수' if response['side'] == 'bid' else '매도'} 주문 {response['state']} 상태\n"
                        f"> 주문단가: {format_number(response['price'])}\n"
                        f"> 주문시간: {datetime.fromisoformat(response['created_at']).strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"> 주문량: {format_number(response['volume'])}, 채결량: {format_number(response['executed_volume'])}, 잔량: {format_number(response['remaining_volume'])}"
                    )
                    text_lines.append((summary, response['uuid']))

        return text_lines if text_lines else text_lines.append(('', ''))
    except Exception as e:
        return f"체결 대기 주문조회 실패: {e}"
    finally:
        db.close()
        
def order_update(cust_nm: str, market_name: str, order_no: str, price: float) -> str:
    db = SessionLocal()
    
    try:
        # 고객명에 의한 고객정보 조회
        cust_info = cust_mng_service.get_cust_info_by_cust_nm(db, cust_nm, market_name)

        # access key
        access_key = cust_info[4]
        # secret_key
        secret_key = cust_info[5]

        text_lines = []
        
        params = {
            'prev_order_uuid': order_no,
            'new_ord_type': 'limit',
            'new_price': str(price),
            'new_volume': 'remain_only',
        }
        
        query_string = unquote(urlencode(params, doseq=True)).encode("utf-8")

        m = hashlib.sha512()
        m.update(query_string)
        query_hash = m.hexdigest()

        payload = {
            'access_key': access_key,
            'nonce': str(uuid.uuid4()),
            'query_hash': query_hash,
            'query_hash_alg': 'SHA512',
        }

        jwt_token = jwt.encode(payload, secret_key)
        authorization = 'Bearer {}'.format(jwt_token)
        headers = {
        'Authorization': authorization,
        }

        # 주문 취소 후 재주문
        response = requests.post(upbit_api_url + '/v1/orders/cancel_and_new', json=params, headers=headers).json()
            
        if "new_order_uuid" in response:
            ord_no  = response["new_order_uuid"]  # 신규주문 ID
            time.sleep(1)
            order_status = get_order(access_key, secret_key, ord_no)
            print("주문 상태:", order_status)

            hold_price = 0
            hold_vol = 0
            prd_nm = response['market'].split('-')[-1] if '-' in response['market'] else response['market']
            # 잔고조회
            raw_balance_list = balance(access_key, secret_key, market_name, prd_nm)
            
            if len(raw_balance_list) > 0:   
                for item in raw_balance_list: 
                    
                    hold_price = float(item['price'])                                   # 매수평균가    
                    hold_vol = float(item['volume']) + float(item['locked_volume'])     # 보유수량 = 주문가능 수량 + 주문묶여있는 수량

            # 주문관리정보 생성
            INSERT_TRADE_INFO = """
                INSERT INTO trade_mng (
                    cust_num, 
                    market_name, 
                    ord_dtm, 
                    ord_no, 
                    prd_nm, 
                    ord_tp,
                    ord_state,
                    ord_count,
                    ord_expect_totamt,
                    ord_price,
                    ord_vol,
                    ord_amt,
                    cut_price,
                    cut_rate,
                    cut_amt,
                    goal_price,
                    goal_rate,
                    goal_amt,
                    margin_vol,
                    executed_vol,
                    remaining_vol,
                    hold_price,
                    hold_vol,
                    paid_fee,
                    ord_type,
                    regr_id, 
                    reg_date, 
                    chgr_id, 
                    chg_date)
                VALUES (
                    :cust_num, 
                    :market_name, 
                    :ord_dtm,
                    :ord_no,
                    :prd_nm,
                    :ord_tp,
                    :ord_state,
                    :ord_count,
                    :ord_expect_totamt,
                    :ord_price,
                    :ord_vol,
                    :ord_amt,
                    :cut_price,
                    :cut_rate,
                    :cut_amt,
                    :goal_price,
                    :goal_rate,
                    :goal_amt,
                    :margin_vol,
                    :executed_vol,
                    :remaining_vol,
                    :hold_price,
                    :hold_vol,
                    :paid_fee,
                    :ord_type,
                    :regr_id,
                    :reg_date,
                    :chgr_id,
                    :chg_date)
            """
            db.execute(text(INSERT_TRADE_INFO), {
                "cust_num": cust_info[0], 
                "market_name": market_name, 
                "ord_dtm": datetime.fromisoformat(order_status['created_at']).strftime("%Y%m%d%H%M%S"), 
                "ord_no": ord_no, 
                "prd_nm": response['market'],
                "ord_tp": "01" if response['side'] == 'bid' else "02",
                "ord_state": order_status['state'],
                "ord_count": 0,
                "ord_expect_totamt": 0,
                "ord_price": price,
                "ord_vol": Decimal(response['remaining_volume']),
                "ord_amt": int(Decimal(str(price)) * Decimal(response['remaining_volume'])),
                "cut_price": 0,
                "cut_rate": 0,
                "cut_amt": 0,
                "goal_price": 0,
                "goal_rate": 0,
                "goal_amt": 0,
                "margin_vol": 0,
                "executed_vol": Decimal(order_status['executed_volume']),
                "remaining_vol": Decimal(order_status['remaining_volume']),
                "hold_price":hold_price,
                "hold_vol":hold_vol,
                "paid_fee": Decimal(order_status['paid_fee']),
                "ord_type":response['ord_type'],
                "regr_id": user_id,
                "reg_date": datetime.now(),
                "chgr_id": user_id,
                "chg_date": datetime.now()
                })
            db.commit()

            summary = (
                f"*{prd_nm}*: {'매수' if response['side'] == 'bid' else '매도'} 주문 {response['state']} 상태\n"
                f"> 정정주문가: {format_number(str(price))}\n"
                f"> 정정주문시간: {datetime.fromisoformat(response['created_at']).strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"> 채결량: {format_number(response['executed_volume'])}, 잔량: {format_number(response['remaining_volume'])}"
            )
            text_lines.append((summary, response['new_order_uuid']))
        else:
            fail_text = f"*주문 취소 후 재주문 실패했습니다.* => {response['error']['message']}"
            text_lines.append({"text": fail_text, "order_no": ""})

        return text_lines if text_lines else text_lines.append(('', ''))
    except Exception as e:
        return f"주문 취소 후 재주문 실패: {e}"
    finally:
        db.close()  

def order_cancel(cust_nm: str, market_name: str, order_no: str) -> str:
    db = SessionLocal()
    
    try:
        # 고객명에 의한 고객정보 조회
        cust_info = cust_mng_service.get_cust_info_by_cust_nm(db, cust_nm, market_name)

        # access key
        access_key = cust_info[4]
        # secret_key
        secret_key = cust_info[5]

        text_lines = []
        if market_name == 'UPBIT':
            params = {
                'uuid': order_no,          # 주문 ID
            }

            query_string = unquote(urlencode(params, doseq=True)).encode("utf-8")

            m = hashlib.sha512()
            m.update(query_string)
            query_hash = m.hexdigest()

            payload = {
                'access_key': access_key,
                'nonce': str(uuid.uuid4()),
                'query_hash': query_hash,
                'query_hash_alg': 'SHA512',
            }

            jwt_token = jwt.encode(payload, secret_key)
            authorization = 'Bearer {}'.format(jwt_token)
            headers = {
                'Authorization': authorization,
            }
            
            # 주문 취소 접수
            if len(requests.delete(upbit_api_url + '/v1/order', params=params, headers=headers).json()) == 1:
                ord_state = requests.delete(upbit_api_url + '/v1/order', params=params, headers=headers).json()['error']['message']
                text_lines.append(
                    f"*주문번호*: {order_no} => [{ord_state}]"
                )
            else:
                ord_state = "주문 취소 정상 처리"
                text_lines.append(
                    f"*주문번호*: {order_no} => [{ord_state}]"
                )

        elif market_name == 'BITHUMB':
            param = dict( uuid=order_no )

            # Generate access token
            query = urlencode(param).encode()
            hash = hashlib.sha512()
            hash.update(query)
            query_hash = hash.hexdigest()
            payload = {
                'access_key': access_key,
                'nonce': str(uuid.uuid4()),
                'timestamp': round(time.time() * 1000), 
                'query_hash': query_hash,
                'query_hash_alg': 'SHA512',
            }   
            jwt_token = jwt.encode(payload, secret_key)
            authorization_token = 'Bearer {}'.format(jwt_token)
            headers = {
                'Authorization': authorization_token
            }

            # 주문 취소 접수
            if len(requests.delete(bithumb_api_url + '/v1/order', params=param, headers=headers).json()) == 1:
                ord_state = requests.delete(upbit_api_url + '/v1/order', params=param, headers=headers).json()['error']['message']
                text_lines.append(
                    f"*주문번호*: {order_no} => [{ord_state}]"
                )
            else:
                ord_state = "주문 취소 정상 처리"
                text_lines.append(
                    f"*주문번호*: {order_no} => [{ord_state}]"
                )

        return "\n".join(text_lines) if text_lines else "주문 취소 접수가 없습니다."  
    except Exception as e:
        return f"주문 취소 접수 실패: {e}"
    finally:
        db.close()
        
from typing import List, Tuple, Optional
from datetime import datetime
from sqlalchemy.sql import text

def get_order_close(
    cust_nm: str,
    market_name: str,
    prd_nm: Optional[str] = None,
    order_no: Optional[str] = None,
    start_dt: Optional[str] = None
) -> List[Tuple[str, str]]:
    db = SessionLocal()
    try:
        # 고객명에 의한 고객정보 조회
        cust_info = cust_mng_service.get_cust_info_by_cust_nm(db, cust_nm, market_name)
        
        text_lines = []
        
        date_obj = datetime.strptime(start_dt, '%Y%m%d')
        start_dt_str = date_obj.strftime('%Y%m%d') + '000000'

        # 동적 조건문 구성
        conditions = [
            "market_name = :market_name",
            "cust_num = :cust_num",
            "ord_dtm >= :start_dt"
        ]

        if prd_nm:
            conditions.append("prd_nm = :prd_nm")
        if order_no:
            conditions.append("(ord_no = :ord_no OR orgn_ord_no = :ord_no)")

        condition_sql = " AND ".join(conditions)

        SELECT_TRADE_MNG = f"""
            SELECT 
                split_part(prd_nm, '-', 2) AS prd_nm, ord_tp, ord_dtm, ord_no, orgn_ord_no, ord_price, ord_vol, ord_amt, hold_price, hold_vol, paid_fee
            FROM trade_mng 
            WHERE ord_state = 'done'
            AND {condition_sql}

            UNION ALL

            SELECT 
                split_part(prd_nm, '-', 2) AS prd_nm, ord_tp, ord_dtm, ord_no, orgn_ord_no, ord_price, ord_vol, ord_amt, hold_price, hold_vol, paid_fee
            FROM trade_mng_hist
            WHERE ord_state = 'done'
            AND {condition_sql}

            ORDER BY ord_dtm
        """

        params = {
            "market_name": market_name,
            "cust_num": cust_info[0],
            "start_dt": start_dt_str,
        }
        if prd_nm:
            params["prd_nm"] = "KRW-" + prd_nm
        if order_no:
            params["ord_no"] = order_no

        trade_mng_list = db.execute(text(SELECT_TRADE_MNG), params).mappings().all()

        for item in trade_mng_list:
            summary = (
                f"*{item['prd_nm']}*: {'매수' if item['ord_tp'] == '01' else '매도'} 주문 {format_number(item['ord_amt'])} 원\n"
                f"> 주문시간: {datetime.strptime(item['ord_dtm'], '%Y%m%d%H%M%S').strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"> 주문단가: {format_number(float(item['ord_price']))}\n"
                f"> 주문량: {format_number(float(item['ord_vol']))}\n"
                f"> 수수료: {format_number(float(item['paid_fee']))}원"
            )
            text_lines.append((summary, item['ord_no']))

        return text_lines if text_lines else [("종료된 주문이 없습니다.", "")]
    
    except Exception as e:
        # 예외 발생 시 상위로 올림 → try-catch로 처리 가능하게
        raise RuntimeError(f"종료된 주문조회 실패: {e}")
    
    finally:
        db.close()
