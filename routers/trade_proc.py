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
from datetime import datetime, timedelta
import os
from sqlalchemy import text, bindparam
from decimal import Decimal, ROUND_HALF_UP, getcontext, ROUND_DOWN, InvalidOperation, localcontext
from typing import Optional
from typing import List, Tuple, Optional
from sqlalchemy.sql import text

upbit_api_url = os.getenv("UPBIT_API")
bithumb_api_url = os.getenv("BITHUMB_API")

user_id = "SLACK_TRADE"

def format_number(value):
    try:
        return f"{float(value):,.2f}" if isinstance(value, float) else f"{int(value):,}"
    except:
        return str(value)

def to_plain_str(value) -> str:
    # str(float)는 소량(예: 8.8e-05)을 지수표기로 만들어 거래소 API가 거부하므로 고정소수점 문자열로 변환
    return format(Decimal(str(value)), 'f')

def get_cust_list(market_name: str) -> List[str]:
    db = SessionLocal()
    try:
        SELECT_CUST_LIST = """
            SELECT cust_nm
            FROM cust_mng
            WHERE market_name = :market_name
            ORDER BY cust_num
        """
        return [row[0] for row in db.execute(text(SELECT_CUST_LIST), {"market_name": market_name}).fetchall()]
    finally:
        db.close()

def get_ticker(market_name: str, prd_nm: str) -> dict:
    api_url = upbit_api_url if market_name == 'UPBIT' else bithumb_api_url
    res = requests.get(api_url + "/v1/ticker", params={"markets": "KRW-" + prd_nm}, timeout=10).json()
    if not isinstance(res, list) or len(res) < 1:
        raise ValueError(f"{prd_nm} 시세 정보를 조회할 수 없습니다.")
    return {
        "trade_price": float(res[0]['trade_price']),
        "low_price": float(res[0]['low_price']),
        "high_price": float(res[0]['high_price']),
    }

def calc_buy_plan(buy_price: float, loss_price: float, buy_amt: float, loss_amt: float) -> dict:
    # 손절금액 기준 / 매수금액 기준 매수량 산정 (Batch/reservebot.py 매수주문 미리보기와 동일 공식, 수량은 소수점 8자리 절사)
    # sell_proc 등이 스레드 전역 Decimal 정밀도를 바꾸므로 로컬 컨텍스트에서 계산
    with localcontext() as ctx:
        ctx.prec = 28
        qty_unit = Decimal("0.00000001")
        bp = Decimal(str(buy_price))
        lp = Decimal(str(loss_price))
        unit_loss = bp - lp

        loss_qty = (Decimal(str(loss_amt)) / unit_loss).quantize(qty_unit, rounding=ROUND_DOWN)
        amt_qty = (Decimal(str(buy_amt)) / bp).quantize(qty_unit, rounding=ROUND_DOWN)

        return {
            "loss_rate": round(float((Decimal(100) - lp / bp * Decimal(100)) * -1), 2),
            "loss_based": {"qty": format(loss_qty, 'f'), "buy_amt": int(bp * loss_qty), "loss_amt": int(unit_loss * loss_qty)},
            "amt_based": {"qty": format(amt_qty, 'f'), "buy_amt": int(bp * amt_qty), "loss_amt": int(unit_loss * amt_qty)},
        }

def get_sell_holding_list(cust_nm: str, market_name: str) -> list:
    db = SessionLocal()
    try:
        req_data = BasicRequest(cust_nm=cust_nm, market_name=market_name)
        result = account_list(req_data, db)

        return [
            {
                "prd_nm": item['name'],
                "volume": float(item['volume']),
                "loss_profit_rate": float(item['loss_profit_rate']),
                "current_amt": int(item['current_amt']),
            }
            for item in result["balance_list"]
            if item['name'] not in ("KRW", "P") and float(item['volume']) > 0
        ]
    finally:
        db.close()

def get_balance(cust_nm: str, market_name: str) -> str:
    db = SessionLocal()
    try:
        req_data = BasicRequest(cust_nm=cust_nm, market_name=market_name)
        result = account_list(req_data, db)

        # 고객명에 의한 고객정보 조회
        cust_info = cust_mng_service.get_cust_info_by_cust_nm(db, cust_nm, market_name)

        # 보유종목의 이탈가/수행가/최종이탈가 조회
        SELECT_BALANCE_PRICE_INFO = """
            SELECT prd_nm, stop_price, action_price, exit_price
            FROM balance_info
            WHERE cust_num = :cust_num
            AND market_name = :market_name
        """
        price_info_rows = db.execute(text(SELECT_BALANCE_PRICE_INFO), {"cust_num": cust_info[0], "market_name": market_name}).mappings().all()
        price_info_map = {row['prd_nm']: row for row in price_info_rows}

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
                price_info = price_info_map.get("KRW-" + item['name'])
                stop_price = price_info['stop_price'] if price_info else None
                action_price = price_info['action_price'] if price_info else None
                exit_price = price_info['exit_price'] if price_info else None

                text_lines.append(
                    f"*{item['name']}*: {format_number(item['price']) if float(item['price']) > 0 else ''}{' ['+format_number(item['trade_price'])+']' if float(item['trade_price']) > 0 else ''}\n"
                    f"> 보유량: {format_number(item['volume'])}{' ('+format_number(item['locked_volume'])+')' if float(item['locked_volume']) > 0  else ''}\n"
                    f"> 원금액: {format_number(item['amt'])}원, 평가액: {format_number(item['current_amt'])}원\n"
                    f"> 손익금: {format_number(item['loss_profit_amt'])}원, 손익률: {item['loss_profit_rate']}%\n"
                    f"> 이탈가: {format_number(stop_price) if stop_price is not None else '-'}, "
                    f"수행가: {format_number(action_price) if action_price is not None else '-'}, "
                    f"최종이탈가: {format_number(exit_price) if exit_price is not None else '-'}"
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
                    volume=to_plain_str(volume),             # 매수량
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
                    volume=to_plain_str(volume),             # 매수량
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

def exchange_auth_headers(market_name: str, access_key: str, secret_key: str, params: Optional[dict] = None) -> dict:
    payload = {
        'access_key': access_key,
        'nonce': str(uuid.uuid4()),
    }
    if market_name == 'BITHUMB':
        payload['timestamp'] = round(time.time() * 1000)
    if params:
        query_string = unquote(urlencode(params, doseq=True)).encode("utf-8")
        payload['query_hash'] = hashlib.sha512(query_string).hexdigest()
        payload['query_hash_alg'] = 'SHA512'

    return {'Authorization': 'Bearer {}'.format(jwt.encode(payload, secret_key))}

def get_open_sell_orders(market_name: str, access_key: str, secret_key: str, prd_nm: str) -> list:
    # 체결대기 주문 조회 (BITHUMB 은 states[] 미지원으로 state=wait 단건 조회)
    if market_name == 'UPBIT':
        api_url, path, params = upbit_api_url, "/v1/orders/open", {'market': "KRW-" + prd_nm, 'states[]': ['wait', 'watch']}
    else:
        api_url, path, params = bithumb_api_url, "/v1/orders", {'market': "KRW-" + prd_nm, 'state': 'wait'}

    orders = requests.get(api_url + path, params=params, headers=exchange_auth_headers(market_name, access_key, secret_key, params), timeout=10).json()
    if not isinstance(orders, list):
        raise RuntimeError(f"체결대기 주문 조회 실패: {orders}")

    return [order for order in orders if order.get('side') == 'ask']

def cancel_open_sell_orders(db, market_name: str, access_key: str, secret_key: str, prd_nm: str) -> Tuple[list, list, bool]:
    # 체결대기 매도주문 취소 → (결과문구, 취소된 주문번호, 성공여부)
    api_url = upbit_api_url if market_name == 'UPBIT' else bithumb_api_url
    lines, cancelled = [], []

    try:
        open_orders = get_open_sell_orders(market_name, access_key, secret_key, prd_nm)
    except Exception as e:
        lines.append({"text": f"*{prd_nm} : 체결대기 매도주문 조회 실패로 매도를 진행하지 않습니다.* => {e}", "order_no": ""})
        return lines, cancelled, False

    for order in open_orders:
        params = {'uuid': order['uuid']}
        try:
            res = requests.delete(api_url + "/v1/order", params=params, headers=exchange_auth_headers(market_name, access_key, secret_key, params), timeout=10).json()
        except Exception as e:
            res = {'error': {'message': str(e)}}

        if isinstance(res, dict) and 'error' in res:
            lines.append({"text": f"*{prd_nm} : 체결대기 매도주문 취소 실패로 매도를 진행하지 않습니다.* => {res['error'].get('message')}", "order_no": order['uuid']})
            return lines, cancelled, False

        cancelled.append(order['uuid'])
        lines.append({"text": f"*{prd_nm}*: 체결대기 매도주문 취소\n> 주문단가: {format_number(float(order.get('price') or 0))}, 잔량: {order.get('remaining_volume')}", "order_no": order['uuid']})

        # 매매관리정보 주문상태 변경
        UPDATE_TRADE_STATE = """
            UPDATE trade_mng
            SET ord_state = 'cancel', chgr_id = :chgr_id, chg_date = :chg_date
            WHERE market_name = :market_name
            AND ord_no = :ord_no
            AND ord_state IN ('wait', 'watch')
        """
        db.execute(text(UPDATE_TRADE_STATE), {"market_name": market_name, "ord_no": order['uuid'], "chgr_id": user_id, "chg_date": datetime.now()})
        db.commit()

    return lines, cancelled, True

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

        # 체결대기 매도주문 취소 후 매도 진행 (주문에 묶인 수량을 매도가능 수량으로 전환)
        cancel_lines, cancelled_orders, cancel_ok = cancel_open_sell_orders(db, market_name, access_key, secret_key, prd_nm)
        text_lines.extend(cancel_lines)
        if not cancel_ok:
            return text_lines

        # 잔고조회 (취소된 주문이 있으면 묶인 수량이 해제될 때까지 최대 약 3초 대기)
        raw_balance_list = balance(access_key, secret_key, market_name, prd_nm)
        for _ in range(6):
            if not cancelled_orders or all(float(item['locked_volume']) == 0 for item in raw_balance_list):
                break
            time.sleep(0.5)
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
                elif gubun == "rate":
                    # 매도비율(1~100) 지정가 매도 : 위 prec=10 설정의 영향을 받지 않도록 로컬 컨텍스트에서 계산
                    with localcontext() as ctx:
                        ctx.prec = 28
                        rate_available = Decimal(str(item['volume'])) - Decimal(str(item['locked_volume']))
                        rate_volume = rate_available * Decimal(str(custom_volumn_rate)) / Decimal("100")
                        volume = float(rate_volume.quantize(Decimal("0.00000001"), rounding=ROUND_DOWN))
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
                        volume=to_plain_str(volume),             # 매도량
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
                        volume=to_plain_str(volume),             # 매도량
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

            # 체결 대기 주문 조회 (마켓별 순회 없이 전체 마켓 대상 1회 조회, 100건 초과시 페이지네이션)
            raw_order_list = []
            page = 1
            while True:
                params = {
                    'states[]': ['wait', 'watch'],
                    'page': page,
                    'limit': 100,
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

                page_order_list = requests.get(upbit_api_url + "/v1/orders/open", params=params, headers=headers).json()

                if not isinstance(page_order_list, list) or len(page_order_list) < 1:
                    break

                raw_order_list.extend(page_order_list)

                if len(page_order_list) < 100:
                    break
                page += 1

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
                chk_trade_mng_list = db.execute(text(SELECT_TRADE_MNG), {"cust_num": cust_info[0], "prd_nm": ord_info['market'], "ord_no": ord_info['uuid'],}).mappings().all()

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
                        "prd_nm": ord_info['market'],
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
                    f"*{ord_info['market'].split('-')[-1]}*: {'매수' if ord_info['side'] == 'bid' else '매도'} 주문 {ord_info['state']} 상태\n"
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
        
        pre_order_info = get_order(access_key, secret_key, order_no)
        print("주문정정 이전 주문 :", pre_order_info)

        # 시장가 매매인 경우
        if price == 0:

            # 시장가 매수
            if pre_order_info['side'] == 'bid':

                # 시장가 매수 총액
                buy_sum = Decimal(pre_order_info['price']) * Decimal(pre_order_info['remaining_volume'])

                params = {
                    'prev_order_uuid': order_no,
                    'new_ord_type': 'price',
                    'new_price': str(buy_sum),
                }

            else:

                params = {
                    'prev_order_uuid': order_no,
                    'new_ord_type': 'market',
                    'new_volume': 'remain_only',
                }

        else:

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
        
def get_holding_prd_list(cust_nm: str, market_name: str) -> List[str]:
    db = SessionLocal()
    try:
        # 고객명에 의한 고객정보 조회
        cust_info = cust_mng_service.get_cust_info_by_cust_nm(db, cust_nm, market_name)

        SELECT_HOLDING_LIST = """
            SELECT prd_nm
            FROM balance_info
            WHERE cust_num = :cust_num
            AND market_name = :market_name
            AND prd_nm != 'KRW-KRW'
            AND hold_volume > 0
            ORDER BY prd_nm
        """
        result = db.execute(text(SELECT_HOLDING_LIST), {"cust_num": cust_info[0], "market_name": market_name}).mappings().all()

        return [item['prd_nm'] for item in result]
    except Exception as e:
        print(f"[get_holding_prd_list] 조회 실패: {e}")
        return []
    finally:
        db.close()

def get_holding_prices(cust_nm: str, market_name: str, prd_nm: str) -> dict:
    db = SessionLocal()
    try:
        # 고객명에 의한 고객정보 조회
        cust_info = cust_mng_service.get_cust_info_by_cust_nm(db, cust_nm, market_name)

        SELECT_HOLDING_PRICES = """
            SELECT stop_price, action_price, exit_price
            FROM balance_info
            WHERE cust_num = :cust_num
            AND market_name = :market_name
            AND prd_nm = :prd_nm
        """
        result = db.execute(text(SELECT_HOLDING_PRICES), {"cust_num": cust_info[0], "market_name": market_name, "prd_nm": prd_nm}).mappings().first()

        if not result:
            return {"stop_price": None, "action_price": None, "exit_price": None}

        return {
            "stop_price": float(result["stop_price"]) if result["stop_price"] is not None else None,
            "action_price": float(result["action_price"]) if result["action_price"] is not None else None,
            "exit_price": float(result["exit_price"]) if result["exit_price"] is not None else None,
        }
    except Exception as e:
        print(f"[get_holding_prices] 조회 실패: {e}")
        return {"stop_price": None, "action_price": None, "exit_price": None}
    finally:
        db.close()

def holding_update(cust_nm: str, market_name: str, prd_nm: str, stop_price: Optional[float] = None, action_price: Optional[float] = None, exit_price: Optional[float] = None, trading_plan: Optional[str] = None) -> str:
    db = SessionLocal()
    try:
        # 고객명에 의한 고객정보 조회
        # cust_info = cust_mng_service.get_cust_info_by_cust_nm(db, cust_nm, market_name)

        # 보유종목 정보 변경 (미입력 가격 항목은 기존값 유지, 매매계획은 선택값으로 변경)
        UPDATE_BALANCE_INFO = """
            UPDATE balance_info
            SET
                stop_price = COALESCE(:stop_price, stop_price),
                action_price = COALESCE(:action_price, action_price),
                exit_price = COALESCE(:exit_price, exit_price),
                trading_plan = :trading_plan,
                chgr_id = :chgr_id,
                chg_date = :chg_date
            WHERE market_name = :market_name
            AND prd_nm = :prd_nm
        """
        result = db.execute(text(UPDATE_BALANCE_INFO), {
            # "cust_num": cust_info[0],
            "market_name": market_name,
            "prd_nm": prd_nm,
            "stop_price": stop_price,
            "action_price": action_price,
            "exit_price": exit_price,
            "trading_plan": trading_plan,
            "chgr_id": user_id,
            "chg_date": datetime.now(),
            })

        if result.rowcount < 1:
            db.rollback()
            return f"*{prd_nm.split('-')[-1] if '-' in prd_nm else prd_nm}* 보유 잔고 정보가 존재하지 않습니다."

        db.commit()

        return f"*{prd_nm.split('-')[-1] if '-' in prd_nm else prd_nm}* 보유종목 정보가 변경되었습니다."
    except Exception as e:
        return f"보유종목 정보 변경 실패: {e}"
    finally:
        db.close()

def get_interest_list(market_name: str) -> str:
    db = SessionLocal()
    try:
        SELECT_INTEREST_LIST = """
            SELECT DISTINCT ON (prd_nm)
                prd_nm, through_price, leave_price, resist_price, support_price,
                trend_high_price, trend_low_price, proc_yn, last_chg_date
            FROM bit_interest_item
            WHERE market_name = :market_name
            ORDER BY prd_nm, interest_day DESC, interest_dtm DESC
        """
        result = db.execute(text(SELECT_INTEREST_LIST), {"market_name": market_name}).mappings().all()

        text_lines = []
        for item in result:
            prd_nm = item['prd_nm'].split('-')[-1] if item['prd_nm'] and '-' in item['prd_nm'] else item['prd_nm']
            text_lines.append(
                f"*{prd_nm}*: {'처리완료' if item['proc_yn'] == 'Y' else '관심등록'}\n"
                f"> 돌파가: {format_number(item['through_price']) if item['through_price'] is not None else '-'}, "
                f"이탈가: {format_number(item['leave_price']) if item['leave_price'] is not None else '-'}\n"
                f"> 저항가: {format_number(item['resist_price']) if item['resist_price'] is not None else '-'}, "
                f"지지가: {format_number(item['support_price']) if item['support_price'] is not None else '-'}\n"
                f"> 추세고가: {format_number(item['trend_high_price']) if item['trend_high_price'] is not None else '-'}, "
                f"추세저가: {format_number(item['trend_low_price']) if item['trend_low_price'] is not None else '-'}\n"
                f"> 최종변경일시: {item['last_chg_date'].strftime('%Y-%m-%d %H:%M:%S')}"
            )

        return "\n".join(text_lines) if text_lines else "등록된 관심종목이 없습니다."
    except Exception as e:
        return f"관심종목 조회 실패: {e}"
    finally:
        db.close()

def interest_update(cust_nm: str = None, market_name: str = None, prd_nm: str = None, through_price: Optional[float] = None, leave_price: Optional[float] = None, resist_price: Optional[float] = None, support_price: Optional[float] = None, trend_high_price: Optional[float] = None, trend_low_price: Optional[float] = None) -> str:
    db = SessionLocal()
    try:
        now = datetime.now()

        # market_name, prd_nm 기준 기존 등록된 관심종목(최신 스냅샷) 조회
        SELECT_LATEST_INTEREST = """
            SELECT interest_day, interest_dtm
            FROM bit_interest_item
            WHERE market_name = :market_name
            AND prd_nm = :prd_nm
            ORDER BY interest_day DESC, interest_dtm DESC
            LIMIT 1
        """
        latest = db.execute(text(SELECT_LATEST_INTEREST), {"market_name": market_name, "prd_nm": prd_nm}).mappings().first()

        if latest:
            # 기존 관심종목 변경 처리 (미입력 항목은 기존값 유지)
            UPDATE_INTEREST = """
                UPDATE bit_interest_item
                SET
                    through_price = COALESCE(:through_price, through_price),
                    leave_price = COALESCE(:leave_price, leave_price),
                    resist_price = COALESCE(:resist_price, resist_price),
                    support_price = COALESCE(:support_price, support_price),
                    trend_high_price = COALESCE(:trend_high_price, trend_high_price),
                    trend_low_price = COALESCE(:trend_low_price, trend_low_price),
                    last_chg_date = :last_chg_date
                WHERE market_name = :market_name
                AND prd_nm = :prd_nm
                AND interest_day = :interest_day
                AND interest_dtm = :interest_dtm
            """
            db.execute(text(UPDATE_INTEREST), {
                "market_name": market_name,
                "prd_nm": prd_nm,
                "interest_day": latest["interest_day"],
                "interest_dtm": latest["interest_dtm"],
                "through_price": through_price,
                "leave_price": leave_price,
                "resist_price": resist_price,
                "support_price": support_price,
                "trend_high_price": trend_high_price,
                "trend_low_price": trend_low_price,
                "last_chg_date": now,
                })
            db.commit()

            result_gubun = "변경"
        else:
            # 신규 관심종목 등록
            INSERT_INTEREST = """
                INSERT INTO bit_interest_item (
                    market_name, prd_nm, interest_day, interest_dtm,
                    through_price, leave_price, resist_price, support_price,
                    trend_high_price, trend_low_price, proc_yn, last_chg_date)
                VALUES (
                    :market_name, :prd_nm, :interest_day, :interest_dtm,
                    :through_price, :leave_price, :resist_price, :support_price,
                    :trend_high_price, :trend_low_price, 'Y', :last_chg_date)
            """
            db.execute(text(INSERT_INTEREST), {
                "market_name": market_name,
                "prd_nm": prd_nm,
                "interest_day": now.strftime("%Y%m%d"),
                "interest_dtm": now.strftime("%H%M%S"),
                "through_price": through_price,
                "leave_price": leave_price,
                "resist_price": resist_price,
                "support_price": support_price,
                "trend_high_price": trend_high_price,
                "trend_low_price": trend_low_price,
                "last_chg_date": now,
                })
            db.commit()

            result_gubun = "등록"

        return f"*{prd_nm.split('-')[-1] if '-' in prd_nm else prd_nm}* 관심종목이 {result_gubun}되었습니다."
    except Exception as e:
        return f"관심종목 등록/변경 실패: {e}"
    finally:
        db.close()

def get_order_close(
    cust_nm: str,
    market_name: str,
    prd_nm: Optional[str] = None,
    order_no: Optional[str] = None,
    start_dt: Optional[str] = None,
    end_dt: Optional[str] = None
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

        if end_dt:
            conditions.append("ord_dtm <= :end_dt")
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
        if end_dt:
            params["end_dt"] = datetime.strptime(end_dt, '%Y%m%d').strftime('%Y%m%d') + '235959'
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

# ────────────────────────────────────────────────────────────────────────────
# 추적관리 (bit_trading_trail)
# ────────────────────────────────────────────────────────────────────────────
def _fmt(value) -> str:
    # 정수는 천단위 콤마, 소수는 소수점 8자리까지 표시 (불필요한 0 제거)
    number = float(value)
    if number == int(number):
        return f"{int(number):,}"
    return f"{number:,.8f}".rstrip("0").rstrip(".")

TRAIL_ACTIVE_TPS = ('1', '2', 'L')
TRAIL_PAUSED_TPS = ('P', 'C', 'U')

def get_business_day(market_name: str, now: Optional[datetime] = None) -> datetime:
    # trail_day 기준일 : UPBIT는 당일 09:00부터 익일 08:59까지, BITHUMB는 당일 00:00부터 23:59까지 (frm_svc/bitTradingSet.py 와 동일 규칙)
    now = now or datetime.now()
    if market_name == 'UPBIT' and now.hour < 9:
        now = now - timedelta(days=1)
    return now

def get_trail_days(market_name: str, now: Optional[datetime] = None) -> Tuple[str, str]:
    # (현재 영업일, 전 영업일) YYYYMMDD
    biz_dt = get_business_day(market_name, now)
    return biz_dt.strftime("%Y%m%d"), (biz_dt - timedelta(days=1)).strftime("%Y%m%d")

def _trail_cust(db, cust_nm: str, market_name: str) -> Tuple[str, str]:
    cust_info = cust_mng_service.get_cust_info_by_cust_nm(db, cust_nm, market_name)
    if not cust_info:
        raise ValueError(f"[{market_name}] {cust_nm} 고객정보가 없습니다.")
    return cust_info[0], cust_info[3]

def _prd_label(prd_nm: str) -> str:
    return prd_nm.split('-')[-1] if prd_nm and '-' in prd_nm else prd_nm

TRAIL_ROW_COLUMNS = """
    id, prd_nm, trail_day, trail_dtm, trail_tp, basic_price, basic_vol, basic_amt,
    stop_price, action_price, exit_price, trail_plan, loss_amt, trade_result
"""

def _wait_state_reset_sql() -> str:
    # 변경된 이탈가/수행가 기준으로 다시 판단하도록 대기 중인 이탈 감지 상태 초기화 (분봉 처리 위치 last_min_key 만 유지)
    return "wait_state = jsonb_strip_nulls(jsonb_build_object('last_min_key', wait_state->'last_min_key'))"

def _clear_same_state_rows(db, cust_num: str, market_name: str, prd_nm: str, trail_day: str, trail_tp: str, keep_id: int) -> int:
    # (cust_num, market_name, prd_nm, trail_day, trail_tp) 유니크 제약 충돌 방지 : 변경할 상태와 같은 상태의 기존 행 정리
    result = db.execute(text("""
        DELETE FROM bit_trading_trail
        WHERE cust_num = :cust_num AND market_name = :market_name AND prd_nm = :prd_nm
        AND trail_day = :trail_day AND trail_tp = :trail_tp AND id <> :keep_id
    """), {"cust_num": cust_num, "market_name": market_name, "prd_nm": prd_nm, "trail_day": trail_day, "trail_tp": trail_tp, "keep_id": keep_id})
    return result.rowcount

def _latest_trail_row(db, cust_num: str, market_name: str, prd_nm: str, trail_day: str, trail_tps: tuple):
    return db.execute(text(f"""
        SELECT {TRAIL_ROW_COLUMNS}
        FROM bit_trading_trail
        WHERE cust_num = :cust_num AND market_name = :market_name AND prd_nm = :prd_nm
        AND trail_day = :trail_day AND trail_tp IN :trail_tps AND basic_vol > 0
        ORDER BY trail_dtm DESC, id DESC
        LIMIT 1
    """).bindparams(bindparam("trail_tps", expanding=True)),
        {"cust_num": cust_num, "market_name": market_name, "prd_nm": prd_nm, "trail_day": trail_day, "trail_tps": list(trail_tps)}).mappings().first()

def get_trail_register_targets(cust_nm: str, market_name: str) -> list:
    # 추적등록 대상 : balance_info 보유종목 (매매계획 홀딩·투자 제외)
    db = SessionLocal()
    try:
        cust_num, _ = _trail_cust(db, cust_nm, market_name)
        return [dict(row) for row in db.execute(text("""
            SELECT prd_nm, hold_price, hold_volume, hold_amt, stop_price, action_price, exit_price
            FROM balance_info
            WHERE cust_num = :cust_num AND market_name = :market_name
            AND prd_nm != 'KRW-KRW'
            AND (trading_plan IS NULL OR trading_plan NOT IN ('i', 'h'))
            AND hold_volume > 0
            ORDER BY prd_nm
        """), {"cust_num": cust_num, "market_name": market_name}).mappings().all()]
    finally:
        db.close()

def get_trail_rows(cust_nm: str, market_name: str, trail_tps: Optional[tuple] = None) -> Tuple[str, list]:
    # 현재 영업일 매매추적정보 조회 → (영업일, 행 리스트)
    # trail_tps 지정시 해당 상태의 보유수량 존재 대상을 종목별 최신 1건으로 조회 (추적변경/재개/멈춤 대상)
    db = SessionLocal()
    try:
        cust_num, _ = _trail_cust(db, cust_nm, market_name)
        today, _ = get_trail_days(market_name)
        params = {"cust_num": cust_num, "market_name": market_name, "trail_day": today}

        if trail_tps:
            query = text(f"""
                SELECT DISTINCT ON (prd_nm) {TRAIL_ROW_COLUMNS}
                FROM bit_trading_trail
                WHERE cust_num = :cust_num AND market_name = :market_name AND trail_day = :trail_day
                AND trail_tp IN :trail_tps AND basic_vol > 0
                ORDER BY prd_nm, trail_dtm DESC, id DESC
            """).bindparams(bindparam("trail_tps", expanding=True))
            params["trail_tps"] = list(trail_tps)
        else:
            query = text(f"""
                SELECT {TRAIL_ROW_COLUMNS}
                FROM bit_trading_trail
                WHERE cust_num = :cust_num AND market_name = :market_name AND trail_day = :trail_day
                ORDER BY prd_nm, trail_dtm, id
            """)

        return today, [dict(row) for row in db.execute(query, params).mappings().all()]
    finally:
        db.close()

def get_trail_row(cust_nm: str, market_name: str, prd_nm: str, trail_tps: tuple) -> Tuple[str, Optional[dict]]:
    db = SessionLocal()
    try:
        cust_num, _ = _trail_cust(db, cust_nm, market_name)
        today, _ = get_trail_days(market_name)
        row = _latest_trail_row(db, cust_num, market_name, prd_nm, today, trail_tps)
        return today, dict(row) if row else None
    finally:
        db.close()

def trail_register(cust_nm: str, market_name: str, prd_nm: str, stop_price: float, action_price: float, exit_price: float, trail_plan: float) -> str:
    db = SessionLocal()
    label = _prd_label(prd_nm)
    try:
        cust_num, acct_no = _trail_cust(db, cust_nm, market_name)
        today, _ = get_trail_days(market_name)

        holding = db.execute(text("""
            SELECT acct_no, hold_price, hold_volume, hold_amt
            FROM balance_info
            WHERE cust_num = :cust_num AND market_name = :market_name AND prd_nm = :prd_nm
            AND (trading_plan IS NULL OR trading_plan NOT IN ('i', 'h'))
            AND hold_volume > 0
        """), {"cust_num": cust_num, "market_name": market_name, "prd_nm": prd_nm}).mappings().first()
        if not holding:
            return f"*{label}* : 보유종목(매매계획 홀딩·투자 제외)에 존재하지 않아 추적등록을 할 수 없습니다."

        if _latest_trail_row(db, cust_num, market_name, prd_nm, today, TRAIL_ACTIVE_TPS):
            return f"*{label}* : 오늘({today}) 진행 중인 추적정보가 있습니다. 추적변경을 사용하세요."

        basic_price = float(holding['hold_price'])
        basic_vol = float(holding['hold_volume'])
        loss_amt = int((basic_price - stop_price) * basic_vol)
        now = datetime.now()

        db.execute(text("""
            INSERT INTO bit_trading_trail (
                acct_no, cust_num, market_name, prd_nm,
                trail_day, trail_dtm, trail_tp,
                basic_price, basic_vol, basic_amt,
                stop_price, action_price, exit_price, trade_tp, loss_amt, trail_plan,
                regr_id, reg_date, chgr_id, chg_date
            ) VALUES (
                :acct_no, :cust_num, :market_name, :prd_nm,
                :trail_day, :trail_dtm, '1',
                :basic_price, :basic_vol, :basic_amt,
                :stop_price, :action_price, :exit_price, 'M', :loss_amt, :trail_plan,
                :user_id, :now, :user_id, :now
            )
        """), {
            "acct_no": holding['acct_no'] or acct_no, "cust_num": cust_num, "market_name": market_name, "prd_nm": prd_nm,
            "trail_day": today, "trail_dtm": now.strftime("%H%M%S"),
            "basic_price": basic_price, "basic_vol": basic_vol, "basic_amt": holding['hold_amt'],
            "stop_price": stop_price, "action_price": action_price, "exit_price": exit_price,
            "loss_amt": loss_amt, "trail_plan": trail_plan, "user_id": user_id, "now": now,
        })
        db.commit()

        return (
            f"*{label}* 추적등록 완료 (영업일 {today}, 추적대기)\n"
            f"> 기준가: {_fmt(basic_price)}, 보유량: {_fmt(basic_vol)}, 기준금액: {_fmt(holding['hold_amt'])}원\n"
            f"> 이탈가: {_fmt(stop_price)}, 수행가: {_fmt(action_price)}, 최종이탈가: {_fmt(exit_price)}, 매도비율: {_fmt(trail_plan)}%\n"
            f"> 손실금액: {_fmt(loss_amt)}원"
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def trail_change(cust_nm: str, market_name: str, prd_nm: str, stop_price: float, action_price: float, exit_price: float, trail_plan: float) -> str:
    # 추적변경 : 추적상태는 1(추적대기)로 변경하되 2(목표가 돌파 후 트레일링)는 유지, trail_dtm 현재시각 갱신
    db = SessionLocal()
    label = _prd_label(prd_nm)
    try:
        cust_num, _ = _trail_cust(db, cust_nm, market_name)
        today, _ = get_trail_days(market_name)
        row = _latest_trail_row(db, cust_num, market_name, prd_nm, today, TRAIL_ACTIVE_TPS)
        if not row:
            return f"*{label}* : 오늘({today}) 추적변경 대상(추적대기·트레일링·장기추적)이 없습니다."

        new_tp = '2' if row['trail_tp'] == '2' else '1'
        cleared = _clear_same_state_rows(db, cust_num, market_name, prd_nm, today, new_tp, row['id'])
        loss_amt = int((float(row['basic_price']) - stop_price) * float(row['basic_vol']))

        db.execute(text(f"""
            UPDATE bit_trading_trail SET
                stop_price = :stop_price, action_price = :action_price, exit_price = :exit_price,
                trail_plan = :trail_plan, loss_amt = :loss_amt, trail_tp = :trail_tp, trail_dtm = :trail_dtm,
                {_wait_state_reset_sql()}, chgr_id = :user_id, chg_date = :now
            WHERE id = :id
        """), {
            "stop_price": stop_price, "action_price": action_price, "exit_price": exit_price, "trail_plan": trail_plan,
            "loss_amt": loss_amt, "trail_tp": new_tp, "trail_dtm": datetime.now().strftime("%H%M%S"),
            "user_id": user_id, "now": datetime.now(), "id": row['id'],
        })
        db.commit()

        return (
            f"*{label}* 추적변경 완료 (추적상태: {row['trail_tp']} → {new_tp})\n"
            f"> 이탈가: {_fmt(stop_price)}, 수행가: {_fmt(action_price)}, 최종이탈가: {_fmt(exit_price)}, 매도비율: {_fmt(trail_plan)}%"
            + (f"\n> 같은 상태의 기존 추적정보 {cleared}건 정리" if cleared else "")
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def trail_resume(cust_nm: str, market_name: str, prd_nm: str, stop_price: float, action_price: float, exit_price: float, trail_tp: str) -> str:
    # 추적재개 : 멈춤·취소·변경(P/C/U) 대상을 선택한 추적상태(L/1/2)로 변경
    db = SessionLocal()
    label = _prd_label(prd_nm)
    try:
        if trail_tp not in TRAIL_ACTIVE_TPS:
            raise ValueError("추적상태는 장기추적(L), 추적대기(1), 트레일링(2) 중에서 선택해주세요.")

        cust_num, _ = _trail_cust(db, cust_nm, market_name)
        today, _ = get_trail_days(market_name)
        row = _latest_trail_row(db, cust_num, market_name, prd_nm, today, TRAIL_PAUSED_TPS)
        if not row:
            return f"*{label}* : 오늘({today}) 추적재개 대상(멈춤·취소·변경)이 없습니다."
        if _latest_trail_row(db, cust_num, market_name, prd_nm, today, TRAIL_ACTIVE_TPS):
            return f"*{label}* : 오늘({today}) 이미 진행 중인 추적정보가 있어 재개할 수 없습니다. 추적변경을 사용하세요."

        cleared = _clear_same_state_rows(db, cust_num, market_name, prd_nm, today, trail_tp, row['id'])

        db.execute(text(f"""
            UPDATE bit_trading_trail SET
                stop_price = :stop_price, action_price = :action_price, exit_price = :exit_price,
                trail_tp = :trail_tp, trail_dtm = :trail_dtm,
                {_wait_state_reset_sql()}, chgr_id = :user_id, chg_date = :now
            WHERE id = :id
        """), {
            "stop_price": stop_price, "action_price": action_price, "exit_price": exit_price,
            "trail_tp": trail_tp, "trail_dtm": datetime.now().strftime("%H%M%S"),
            "user_id": user_id, "now": datetime.now(), "id": row['id'],
        })
        db.commit()

        return (
            f"*{label}* 추적재개 완료 (추적상태: {row['trail_tp']} → {trail_tp})\n"
            f"> 이탈가: {_fmt(stop_price)}, 수행가: {_fmt(action_price)}, 최종이탈가: {_fmt(exit_price)}"
            + (f"\n> 같은 상태의 기존 추적정보 {cleared}건 정리" if cleared else "")
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def trail_pause(cust_nm: str, market_name: str, prd_nm: str) -> str:
    # 추적멈춤 : 진행 중(1/2/L) 대상을 P(멈춤)로 변경
    db = SessionLocal()
    label = _prd_label(prd_nm)
    try:
        cust_num, _ = _trail_cust(db, cust_nm, market_name)
        today, _ = get_trail_days(market_name)
        row = _latest_trail_row(db, cust_num, market_name, prd_nm, today, TRAIL_ACTIVE_TPS)
        if not row:
            return f"*{label}* : 오늘({today}) 추적멈춤 대상(추적대기·트레일링·장기추적)이 없습니다."

        cleared = _clear_same_state_rows(db, cust_num, market_name, prd_nm, today, 'P', row['id'])

        db.execute(text("""
            UPDATE bit_trading_trail SET trail_tp = 'P', chgr_id = :user_id, chg_date = :now
            WHERE id = :id
        """), {"user_id": user_id, "now": datetime.now(), "id": row['id']})
        db.commit()

        return (
            f"*{label}* 추적멈춤 완료 (추적상태: {row['trail_tp']} → P)"
            + (f"\n> 같은 상태의 기존 추적정보 {cleared}건 정리" if cleared else "")
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def trail_delete_today(cust_nm: str, market_name: str) -> Tuple[str, int]:
    # 추적삭제 : 고객의 현재 영업일 추적정보 전체 삭제 → (영업일, 삭제건수)
    db = SessionLocal()
    try:
        cust_num, _ = _trail_cust(db, cust_nm, market_name)
        today, _ = get_trail_days(market_name)
        result = db.execute(text("""
            DELETE FROM bit_trading_trail
            WHERE cust_num = :cust_num AND market_name = :market_name AND trail_day = :trail_day
        """), {"cust_num": cust_num, "market_name": market_name, "trail_day": today})
        db.commit()
        return today, result.rowcount
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def trail_prepare(cust_nm: str, market_name: str) -> dict:
    # 추적준비 : frm_svc/bitTradingSet.py 의 매매추적정보 생성 로직을 선택 고객 대상으로 수행
    # 전일 추적상태 승계 : 3/L → L, P/C/U → P, 그 외 → 1
    # 오늘 추적정보가 이미 있는 종목은 생성하지 않음 (멈춤 등 당일 변경 상태 보존)
    db = SessionLocal()
    try:
        cust_num, _ = _trail_cust(db, cust_nm, market_name)
        today, prev_day = get_trail_days(market_name)

        targets = db.execute(text("""
            SELECT
                A.acct_no, A.prd_nm, A.hold_price, A.hold_volume, A.hold_amt,
                A.stop_price, A.action_price, A.exit_price,
                T.trail_tp AS prev_trail_tp,
                EXISTS (
                    SELECT 1 FROM bit_trading_trail E
                    WHERE E.cust_num = A.cust_num AND E.market_name = A.market_name
                    AND E.prd_nm = A.prd_nm AND E.trail_day = :today
                ) AS exists_today
            FROM balance_info A
            LEFT JOIN LATERAL (
                SELECT trail_tp
                FROM bit_trading_trail
                WHERE cust_num = A.cust_num
                AND market_name = A.market_name
                AND prd_nm = A.prd_nm
                AND trail_day = :prev_day
                AND trail_tp IN ('1','2','3','L','P','C','U')
                ORDER BY trail_dtm DESC
                LIMIT 1
            ) T ON true
            WHERE A.cust_num = :cust_num
            AND A.market_name = :market_name
            AND A.prd_nm != 'KRW-KRW'
            AND (A.trading_plan IS NULL OR A.trading_plan NOT IN ('i', 'h'))
            AND A.hold_volume > 0
            ORDER BY A.prd_nm
        """), {"cust_num": cust_num, "market_name": market_name, "today": today, "prev_day": prev_day}).mappings().all()

        created, skipped = [], []
        now = datetime.now()
        for row in targets:
            if row['exists_today']:
                skipped.append(_prd_label(row['prd_nm']))
                continue

            basic_price = float(row['hold_price'] or 0)
            basic_vol = float(row['hold_volume'] or 0)
            stop_price = float(row['stop_price'] or 0)
            action_price = float(row['action_price'] or 0)
            exit_price = float(row['exit_price'] or 0)
            loss_amt = int((basic_price - stop_price) * basic_vol) if stop_price > 0 else 0
            prev_tp = row['prev_trail_tp']
            trail_tp = 'L' if prev_tp in ('3', 'L') else 'P' if prev_tp in TRAIL_PAUSED_TPS else '1'

            result = db.execute(text("""
                INSERT INTO bit_trading_trail (
                    acct_no, cust_num, market_name, prd_nm,
                    trail_day, trail_dtm, trail_tp,
                    basic_price, basic_vol, basic_amt,
                    stop_price, action_price, exit_price, trade_tp, loss_amt,
                    regr_id, reg_date, chgr_id, chg_date
                ) VALUES (
                    :acct_no, :cust_num, :market_name, :prd_nm,
                    :trail_day, :trail_dtm, :trail_tp,
                    :basic_price, :basic_vol, :basic_amt,
                    :stop_price, :action_price, :exit_price, 'M', :loss_amt,
                    :user_id, :now, :user_id, :now
                )
                ON CONFLICT (cust_num, market_name, prd_nm, trail_day, trail_tp) DO NOTHING
            """), {
                "acct_no": row['acct_no'], "cust_num": cust_num, "market_name": market_name, "prd_nm": row['prd_nm'],
                "trail_day": today, "trail_dtm": '090000' if market_name == 'UPBIT' else '000000', "trail_tp": trail_tp,
                "basic_price": basic_price, "basic_vol": basic_vol, "basic_amt": row['hold_amt'],
                "stop_price": stop_price, "action_price": action_price, "exit_price": exit_price, "loss_amt": loss_amt,
                "user_id": user_id, "now": now,
            })
            if result.rowcount > 0:
                created.append(f"{_prd_label(row['prd_nm'])}({trail_tp})")
            else:
                skipped.append(_prd_label(row['prd_nm']))

        db.commit()
        return {"trail_day": today, "target": len(targets), "created": created, "skipped": skipped}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
