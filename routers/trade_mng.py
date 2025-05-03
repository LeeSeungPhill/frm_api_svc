from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from config import db as config
from models.trade_mng import dividion_sell, dividion_buy, open_order, cancel_order, close_order, account_list, TradeResponse, SellResponse, BalanceResponse, OrderResponse, trade_plan, TradePlanResponse
from services import cust_mng_service

from datetime import date, datetime, timezone
from sqlalchemy import text
from config.db import get_db

import jwt
import hashlib
import os
import requests
import uuid
from urllib.parse import urlencode, unquote
from decimal import Decimal, ROUND_DOWN
from dotenv import load_dotenv
load_dotenv()
from typing import List, Optional
import time
import pytz
import ccxt
import pandas as pd
import json

router = APIRouter()

upbit_api_url = os.getenv("UPBIT_API")
bithumb_api_url = os.getenv("BITHUMB_API")

user_id = "FASTAPI"

# 잔고 조회
@router.post("/account_list", response_model=BalanceResponse)
def account_list(trade_mng: account_list, db: Session = Depends(config.get_db)):
    try:
        # 고객명에 의한 고객정보 조회
        cust_info = cust_mng_service.get_cust_info_by_cust_nm(db, trade_mng.cust_nm, trade_mng.market_name)

        # access key
        access_key = cust_info[4]
        # secret_key
        secret_key = cust_info[5]

        # 잔고조회
        raw_balance_list = balance(access_key, secret_key, trade_mng.market_name)

        balance_list = {"balance_list": [
                {
                    "name": item["name"],
                    "price": item["price"],
                    "volume": item["volume"],
                    "amt": item["amt"],
                    "locked_volume" : item['locked_volume'],
                    "locked_amt" : item['locked_amt'],
                    "trade_price": item["trade_price"],
                    "current_amt": item["current_amt"],
                    "loss_profit_amt": item["loss_profit_amt"],
                    "loss_profit_rate": item["loss_profit_rate"]
                }
            for item in raw_balance_list
        ]}

        return balance_list

    except ValueError:
        raise HTTPException(status_code=400, detail="Cust Info Already Registered")

# 매매 계획
@router.post("/trade_plan", response_model=TradePlanResponse)
def order_plan(plan: trade_plan, db: Session = Depends(config.get_db)):
    try:
        # 고객명에 의한 고객정보 조회
        cust_info = cust_mng_service.get_cust_info_by_cust_nm(db, plan.cust_nm, plan.market_name)

        # access key
        access_key = cust_info[4]
        # secret_key
        secret_key = cust_info[5]

        plan_list = []

        # 매수수량 계산 : B1 분할정액 매수, B2 손실율 매수
        if plan.plan_tp == 'B':
            buy_division_amt_except_fee = (int(plan.plan_tot_amt) * Decimal('0.9995')).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)
            # 분할 매수 수량
            plan_vol = (buy_division_amt_except_fee / plan.plan_price).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)
            
            # 매매예정금액
            plan_amt = int(plan.plan_price * plan_vol)

            plan_param = {
                "cust_num": cust_info[0],
                "cust_nm": cust_info[1],
                "market_name": plan.market_name,
                "prd_nm": plan.prd_nm, 
                "price": 0, 
                "volume": 0,
                "plan_tp": "B1",
                "plan_price": plan.plan_price,
                "plan_vol": plan_vol,
                "plan_amt": plan_amt,
                "regist_price": plan.regist_price,
                "support_price": plan.support_price,
            }

            plan_list.append(plan_param)

            # 손실금액(종목당 손실금액)
            # cut_amt = int(buy_division_amt_except_fee * (100 - (plan.support_price / plan.plan_price) * 100) / 100)
            cut_amt = 50000
            # 손실율 매수 수량
            plan_vol = cut_amt / (plan.plan_price - plan.support_price)

            # 매매예정금액
            plan_amt = int(plan.plan_price * plan_vol)

            plan_param = {
                "cust_num": cust_info[0],
                "cust_nm": cust_info[1],
                "market_name": plan.market_name,
                "prd_nm": plan.prd_nm, 
                "price": 0, 
                "volume": 0,
                "plan_tp": "B2",
                "plan_price": plan.plan_price,
                "plan_vol": plan_vol,
                "plan_amt": plan_amt,
                "regist_price": plan.regist_price,
                "support_price": plan.support_price,
            }

            plan_list.append(plan_param)

        # 매도수량 계산 : S1 안전마진 매도, S2 저항대도달 및 지지대이탈 매도
        elif plan.plan_tp == 'S':
            # 잔고조회
            raw_balance_list = balance(access_key, secret_key, plan.market_name)

            for item in raw_balance_list:
                if plan.prd_nm[4:] == item["name"]:
                    
                    # 손실금액
                    cut_amt = abs(int(item["amt"] * (100 - (plan.support_price / Decimal(item["price"])) * 100) / 100))
                    # 안전마진 매도 수량
                    plan_vol = (Decimal(cut_amt) / (plan.regist_price - plan.support_price)).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)
                    
                    if item["volume"] < plan_vol:
                        plan_vol = item["volume"]

                    # 매매예정금액
                    plan_amt = int(plan.plan_price * Decimal(plan_vol))

                    plan_param = {
                        "cust_num": cust_info[0],
                        "cust_nm": cust_info[1],
                        "market_name": plan.market_name,
                        "prd_nm": plan.prd_nm, 
                        "price": item["price"], 
                        "volume": item["volume"],
                        "plan_tp": "S1",
                        "plan_price": plan.plan_price,
                        "plan_vol": plan_vol,
                        "plan_amt": plan_amt,
                        "regist_price": plan.regist_price,
                        "support_price": plan.support_price,
                    }

                    plan_list.append(plan_param)

                    plan_vol = item["volume"]

                    # 매매예정금액
                    plan_amt = int(plan.plan_price * Decimal(plan_vol))

                    plan_param = {
                        "cust_num": cust_info[0],
                        "cust_nm": cust_info[1],
                        "market_name": plan.market_name,
                        "prd_nm": plan.prd_nm, 
                        "price": item["price"], 
                        "volume": item["volume"],
                        "plan_tp": "S2",
                        "plan_price": plan.plan_price,
                        "plan_vol": plan_vol,
                        "plan_amt": plan_amt,
                        "regist_price": plan.regist_price,
                        "support_price": plan.support_price,
                    }

                    plan_list.append(plan_param)
                
        # 매매예정정보 백업 및 생성
        create_trade_plan(plan_list, db)
        
        # 잔고정보 미존재 대상 매매처리된 매매예정정보 백업 처리
        regist_trade_plan_hist(cust_info[0], cust_info[1], plan.market_name, plan.prd_nm, db)

        trade_plan_list = {"trade_plan_list": [
                {
                    "market_name": item["market_name"],
                    "prd_nm": item["prd_nm"],
                    "price": item["price"],
                    "volume": item["volume"],
                    "plan_tp" : item['plan_tp'],
                    "plan_price" : item['plan_price'],
                    "plan_vol": Decimal(str(item["plan_vol"])).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN),
                    "plan_amt": item["plan_amt"],
                    "regist_price": item["regist_price"],
                    "support_price": item["support_price"]
                }
            for item in plan_list
        ]}

        return trade_plan_list

    except ValueError:
        raise HTTPException(status_code=400, detail="Cust Info Already Registered")

# 분할매도 주문
@router.post("/division_sell", response_model=SellResponse)
def division_sell(trade_mng: dividion_sell, db: Session = Depends(config.get_db)):
    try:
        # 고객명에 의한 고객정보 조회
        cust_info = cust_mng_service.get_cust_info_by_cust_nm(db, trade_mng.cust_nm, trade_mng.market_name)

        # access key
        access_key = cust_info[4]
        # secret_key
        secret_key = cust_info[5]

        # 잔고조회
        raw_balance_list = balance(access_key, secret_key, trade_mng.market_name, trade_mng.prd_nm[4:])

        volume = 0

        for item in raw_balance_list:
            if trade_mng.prd_nm[4:] == item["name"]:
                # 1. 매도수량 분할 횟수가 1 인 경우
                if trade_mng.ord_count == 1:
                    # 보유수량 전체 매도수량 설정
                    volume = item["volume"]
                # 2. 매도수량 분할 횟수가 2 이상인 경우
                elif trade_mng.ord_count >= 2:
                    # 보유수량 대비 분할횟수에 따른 매도수량 설정
                    volume = item["volume"] / trade_mng.ord_count
                else:
                    # 2. 매도수량이 존재하는 경우
                    if trade_mng.ord_vol > 0:
                        if item["volume"] >= trade_mng.ord_vol:
                            # 입력 매도수량 설정
                            volume = trade_mng.ord_vol
                    else:
                        # 손절금액
                        cut_amt = abs(int(item["amt"] * (100 - (trade_mng.cut_price / Decimal(item["price"])) * 100) / 100))
                        # 3. 안전마진 매도 수량
                        volume = (Decimal(cut_amt) / (trade_mng.goal_price - trade_mng.cut_price)).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)

        if volume > 0: # 매도물량 존재하는 경우
            print("order available volume : ",volume)

            if trade_mng.market_name == 'UPBIT':
                order_response = place_order(
                    access_key, 
                    secret_key,
                    market=trade_mng.prd_nm,
                    side="ask",                     # 매도
                    volume=str(volume),             # 매도량
                    price=str(trade_mng.ord_price), # 매도가격
                    ord_type="limit"                # 지정가 주문
                )

                print("주문 응답:", order_response)

                if "uuid" in order_response:
                    ord_no  = order_response["uuid"]  # 주문 ID
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
                            :regr_id,
                            :reg_date,
                            :chgr_id,
                            :chg_date)
                    """
                    db.execute(text(INSERT_TRADE_INFO), {
                        "cust_num": cust_info[0], 
                        "market_name": trade_mng.market_name, 
                        "ord_dtm": datetime.fromisoformat(order_status['created_at']).strftime("%Y%m%d%H%M%S"), 
                        "ord_no": ord_no, 
                        "prd_nm": trade_mng.prd_nm,
                        "ord_tp": trade_mng.ord_tp,
                        "ord_state": order_status['state'],
                        "ord_count": trade_mng.ord_count,
                        "ord_expect_totamt": 0,
                        "ord_price": trade_mng.ord_price,
                        "ord_vol": volume,
                        "ord_amt": int(trade_mng.ord_price * Decimal(volume)),
                        "cut_price": 0,
                        "cut_rate": 0,
                        "cut_amt": 0,
                        "goal_price": 0,
                        "goal_rate": 0,
                        "goal_amt": 0,
                        "margin_vol": 0,
                        "executed_vol": Decimal(order_status['executed_volume']),
                        "remaining_vol": Decimal(order_status['remaining_volume']),
                        "regr_id": user_id,
                        "reg_date": datetime.now(),
                        "chgr_id": user_id,
                        "chg_date": datetime.now()
                        })
                    db.commit()

                else:
                    print("주문 실패:", order_response)

            elif trade_mng.market_name == 'BITHUMB':
                order_response = bithumb_order(
                    access_key, 
                    secret_key,
                    market=trade_mng.prd_nm,
                    side="ask",                     # 매도
                    volume=str(volume),             # 매도량
                    price=str(trade_mng.ord_price), # 매도가격
                    ord_type="limit"                # 지정가 주문
                )
            
                print("주문 응답:", order_response)

                if "uuid" in order_response:
                    ord_no  = order_response["uuid"]  # 주문 ID
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
                            :regr_id,
                            :reg_date,
                            :chgr_id,
                            :chg_date)
                    """
                    db.execute(text(INSERT_TRADE_INFO), {
                        "cust_num": cust_info[0], 
                        "market_name": trade_mng.market_name, 
                        "ord_dtm": datetime.fromisoformat(order_status['created_at']).strftime("%Y%m%d%H%M%S"), 
                        "ord_no": ord_no, 
                        "prd_nm": trade_mng.prd_nm,
                        "ord_tp": trade_mng.ord_tp,
                        "ord_state": order_status['state'],
                        "ord_count": trade_mng.ord_count,
                        "ord_expect_totamt": 0,
                        "ord_price": trade_mng.ord_price,
                        "ord_vol": volume,
                        "ord_amt": int(trade_mng.ord_price * Decimal(volume)),
                        "cut_price": 0,
                        "cut_rate": 0,
                        "cut_amt": 0,
                        "goal_price": 0,
                        "goal_rate": 0,
                        "goal_amt": 0,
                        "margin_vol": 0,
                        "executed_vol": Decimal(order_status['executed_volume']),
                        "remaining_vol": Decimal(order_status['remaining_volume']),
                        "regr_id": user_id,
                        "reg_date": datetime.now(),
                        "chgr_id": user_id,
                        "chg_date": datetime.now()
                        })
                    db.commit()

                else:
                    print("주문 실패:", order_response)
                
        else:
            print("sell not available")

        balance_list = {"balance_list": [
                {
                    "name": item["name"],
                    "price": item["price"],
                    "volume": item["volume"],
                    "amt": item["amt"],
                    "locked_volume" : item['locked_volume'],
                    "locked_amt" : item['locked_amt'],
                    "trade_price": item["trade_price"],
                    "current_amt": item["current_amt"],
                    "loss_profit_amt": item["loss_profit_amt"],
                    "loss_profit_rate": item["loss_profit_rate"]
                }
            for item in raw_balance_list
        ]}

        return balance_list

    except ValueError:
        raise HTTPException(status_code=400, detail="Cust Info Already Registered")

# 분할매수 주문
@router.post("/division_buy", response_model=TradeResponse)
def division_buy(trade_mng: dividion_buy, db: Session = Depends(config.get_db)):
    try:
        # 고객명에 의한 고객정보 조회
        cust_info = cust_mng_service.get_cust_info_by_cust_nm(db, trade_mng.cust_nm, trade_mng.market_name)

        # access key
        access_key = cust_info[4]
        # secret_key
        secret_key = cust_info[5]

        # 수수료를 제외한 잔고
        buy_division_amt_except_fee = (int(trade_mng.ord_expect_totamt) * Decimal('0.9995')).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)

        # 매수물량
        buy_vol = (buy_division_amt_except_fee / trade_mng.ord_price).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)
        # 손절금액
        cut_amt = int(buy_division_amt_except_fee * (100 - (trade_mng.cut_price / trade_mng.ord_price) * 100) / 100)
        # 손절율
        cut_rate = (100 - (trade_mng.cut_price / trade_mng.ord_price) * 100).quantize(Decimal('0.01'), rounding=ROUND_DOWN)
        # 목표금액
        goal_amt = int(buy_vol * trade_mng.goal_price) - buy_division_amt_except_fee
        # 목표율
        goal_rate = ((100 - (trade_mng.goal_price / trade_mng.ord_price) * 100) * -1).quantize(Decimal('0.01'), rounding=ROUND_DOWN)
        # 안전마진수량
        margin_vol = (cut_amt / (trade_mng.goal_price - trade_mng.cut_price)).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)

        ord_no = ""
        ord_state = ""

        if buy_division_amt_except_fee > 5000: # 수수료를 제외한 잔고가 5000보다 큰 경우
            print("order available balance : ",buy_division_amt_except_fee)
            print("order volume : ",buy_vol)

            if trade_mng.market_name == 'UPBIT':
                order_response = place_order(
                    access_key, 
                    secret_key, 
                    market=trade_mng.prd_nm,
                    side="bid",                         # 매수
                    volume=str(buy_vol),                # 주문량
                    price=str(trade_mng.ord_price),     # 주문가
                    ord_type="limit"                    # 지정가 주문
                )
                
                print("주문 응답:", order_response)

                if "uuid" in order_response:
                    ord_no  = order_response["uuid"]  # 주문 ID
                    order_status = get_order(access_key, secret_key, ord_no)
                    ord_state = order_status['state']
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
                            :regr_id,
                            :reg_date,
                            :chgr_id,
                            :chg_date)
                    """
                    db.execute(text(INSERT_TRADE_INFO), {
                        "cust_num": cust_info[0], 
                        "market_name": trade_mng.market_name, 
                        "ord_dtm": datetime.fromisoformat(order_status['created_at']).strftime("%Y%m%d%H%M%S"), 
                        "ord_no": ord_no, 
                        "prd_nm": trade_mng.prd_nm,
                        "ord_tp": trade_mng.ord_tp,
                        "ord_state": order_status['state'],
                        "ord_count": trade_mng.ord_count,
                        "ord_expect_totamt": trade_mng.ord_expect_totamt,
                        "ord_price": trade_mng.ord_price,
                        "ord_vol": buy_vol,
                        "ord_amt": int(trade_mng.ord_price * buy_vol),
                        "cut_price": trade_mng.cut_price,
                        "cut_rate": cut_rate,
                        "cut_amt": Decimal(cut_amt),
                        "goal_price": trade_mng.goal_price,
                        "goal_rate": goal_rate,
                        "goal_amt": goal_amt,
                        "margin_vol": margin_vol,
                        "executed_vol": Decimal(order_status['executed_volume']),
                        "remaining_vol": Decimal(order_status['remaining_volume']),
                        "regr_id": user_id,
                        "reg_date": datetime.now(),
                        "chgr_id": user_id,
                        "chg_date": datetime.now()
                        })
                    db.commit()

                else:
                    print("주문 실패:", order_response)
            
            elif trade_mng.market_name == 'BITHUMB':

                order_response = bithumb_order(
                    access_key, 
                    secret_key, 
                    market=trade_mng.prd_nm,
                    side="bid",                         # 매수
                    volume=str(buy_vol),                # 주문량
                    price=str(trade_mng.ord_price),     # 주문가
                    ord_type="limit"                    # 지정가 주문
                )
                
                print("주문 응답:", order_response)

                if "uuid" in order_response:
                    ord_no  = order_response["uuid"]  # 주문 ID
                    order_status = bithumb_get_order(access_key, secret_key, ord_no)
                    ord_state = order_status['state']
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
                            :regr_id,
                            :reg_date,
                            :chgr_id,
                            :chg_date)
                    """
                    db.execute(text(INSERT_TRADE_INFO), {
                        "cust_num": cust_info[0], 
                        "market_name": trade_mng.market_name, 
                        "ord_dtm": datetime.fromisoformat(order_status['created_at']).strftime("%Y%m%d%H%M%S"), 
                        "ord_no": ord_no, 
                        "prd_nm": trade_mng.prd_nm,
                        "ord_tp": trade_mng.ord_tp,
                        "ord_state": order_status['state'],
                        "ord_count": trade_mng.ord_count,
                        "ord_expect_totamt": trade_mng.ord_expect_totamt,
                        "ord_price": trade_mng.ord_price,
                        "ord_vol": buy_vol,
                        "ord_amt": int(trade_mng.ord_price * buy_vol),
                        "cut_price": trade_mng.cut_price,
                        "cut_rate": cut_rate,
                        "cut_amt": Decimal(cut_amt),
                        "goal_price": trade_mng.goal_price,
                        "goal_rate": goal_rate,
                        "goal_amt": goal_amt,
                        "margin_vol": margin_vol,
                        "executed_vol": Decimal(order_status['executed_volume']),
                        "remaining_vol": Decimal(order_status['remaining_volume']),
                        "regr_id": user_id,
                        "reg_date": datetime.now(),
                        "chgr_id": user_id,
                        "chg_date": datetime.now()
                        })
                    db.commit()

                else:
                    print("주문 실패:", order_response)

        return {"ord_no": ord_no, "ord_state": ord_state}
    except ValueError:
        raise HTTPException(status_code=400, detail="Cust Info Already Registered")

# 주문조회
@router.post("/open_order", response_model=OrderResponse)
def open_order(trade_mng: open_order, db: Session = Depends(config.get_db)):

    # 대기 상태 주문관리정보 조회
    SELECT_OPEN_ORDER_INFO = """
        SELECT A.cust_num, A.market_name, A.access_key, A.secret_key, B.id, B.prd_nm, B.ord_state, B.executed_vol, B.remaining_vol, B.ord_no
        FROM cust_mng A LEFT OUTER JOIN trade_mng B 
        ON A.cust_num = B.cust_num AND A.market_name = B.market_name
        WHERE A.cust_nm = :cust_nm AND A.market_name = :market_name AND B.prd_nm = :prd_nm AND B.ord_state IN ('wait' ,'watch')
    """
    chk_ord_list = db.execute(text(SELECT_OPEN_ORDER_INFO), {"cust_nm": trade_mng.cust_nm, "market_name": trade_mng.market_name, "prd_nm": trade_mng.prd_nm,}).mappings().all()

    order_list = []

    for chk_ord in chk_ord_list :
        access_key = chk_ord['access_key']
        secret_key = chk_ord['secret_key']

        # 주문 조회
        if trade_mng.market_name == 'UPBIT':
            order_status = get_order(access_key, secret_key, chk_ord['ord_no'])
        elif trade_mng.market_name == 'BITHUMB':
            order_status = bithumb_get_order(access_key, secret_key, chk_ord['ord_no'])
        ord_state = order_status['state']

        # 체결완료 상태인 경우
        if ord_state == 'done':
            if chk_ord['ord_no'] == order_status['uuid']:
                order_param = {
                    "ord_dtm": datetime.fromisoformat(order_status['trades'][0]['created_at']).strftime("%Y%m%d%H%M%S"),
                    "ord_no": order_status['trades'][0]['uuid'],
                    "prd_nm": order_status['trades'][0]['market'],
                    "ord_tp": '01' if order_status['trades'][0]['side'] == 'bid' else '02',
                    "ord_state": order_status['state'],
                    "ord_price": order_status['trades'][0]['price'],
                    "ord_vol": order_status['trades'][0]['volume'],
                    "executed_vol": order_status['executed_volume'],
                    "remaining_vol": order_status['remaining_volume']
                }
        
                order_list.append(order_param)

                # 주문관리정보 변경 처리
                UPDATE_TRADE_INFO = """
                                    UPDATE trade_mng 
                                    SET 
                                        ord_dtm = :ord_dtm,
                                        ord_no = :ord_no,
                                        orgn_ord_no = :orgn_ord_no,
                                        ord_state = :ord_state,
                                        executed_vol = :executed_vol, 
                                        remaining_vol = :remaining_vol, 
                                        chgr_id = :chgr_id, 
                                        chg_date = :chg_date
                                    WHERE id = :id
                                    AND ord_state = 'wait'
                                    """
                db.execute(text(UPDATE_TRADE_INFO), {
                        "ord_dtm": datetime.fromisoformat(order_status['trades'][0]['created_at']).strftime("%Y%m%d%H%M%S"),
                        "ord_no": order_status['trades'][0]['uuid'],
                        "orgn_ord_no": chk_ord['ord_no'],
                        "ord_state": order_status['state'],
                        "executed_vol": Decimal(order_status['executed_volume']),
                        "remaining_vol": Decimal(order_status['remaining_volume']),
                        "chgr_id": user_id,
                        "chg_date": datetime.now(),
                        "id": chk_ord['id']
                        })
                db.commit()
        
        # 취소 상태인 경우
        elif ord_state == 'cancel':
            if chk_ord['ord_no'] == order_status['uuid']:
                order_param = {
                    "ord_dtm": datetime.fromisoformat(order_status['created_at']).strftime("%Y%m%d%H%M%S"),
                    "ord_no": order_status['uuid'],
                    "prd_nm": order_status['market'],
                    "ord_tp": '01' if order_status['side'] == 'bid' else '02',
                    "ord_state": order_status['state'],
                    "ord_price": order_status['price'],
                    "ord_vol": order_status['volume'],
                    "executed_vol": order_status['executed_volume'],
                    "remaining_vol": order_status['remaining_volume']
                }
        
                order_list.append(order_param)

                # 주문관리정보 변경 처리
                UPDATE_TRADE_INFO = """
                                    UPDATE trade_mng 
                                    SET 
                                        ord_state = :ord_state,
                                        executed_vol = :executed_vol, 
                                        remaining_vol = :remaining_vol, 
                                        chgr_id = :chgr_id, 
                                        chg_date = :chg_date
                                    WHERE id = :id
                                    AND ord_state = 'wait'
                                    """
                db.execute(text(UPDATE_TRADE_INFO), {
                        "ord_state": order_status['state'],
                        "executed_vol": Decimal(order_status['executed_volume']),
                        "remaining_vol": Decimal(order_status['remaining_volume']),
                        "chgr_id": user_id,
                        "chg_date": datetime.now(),
                        "id": chk_ord['id']
                        })
                db.commit()

        else:

            if trade_mng.market_name == 'UPBIT':
                params = {
                    'market': chk_ord['prd_nm'],        # 마켓 ID
                    'states': chk_ord['ord_state'],     # 'wait', 'watch'
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

                if raw_order_list is not None:
                    for item in raw_order_list:
                        if chk_ord['ord_no'] == item['uuid']:

                            order_param = {
                                    "ord_dtm": datetime.fromisoformat(item['created_at']).strftime("%Y%m%d%H%M%S"),
                                    "ord_no": item['uuid'],
                                    "prd_nm": item['market'],
                                    "ord_tp": '01' if item['side'] == 'bid' else '02',
                                    "ord_state": item['state'],
                                    "ord_price": item['price'],
                                    "ord_vol": item['volume'],
                                    "executed_vol": item['executed_volume'],
                                    "remaining_vol": item['remaining_volume']
                                }
                    
                            order_list.append(order_param)

                            if chk_ord['remaining_vol'] != Decimal(item['remaining_volume']) or chk_ord['executed_vol'] != Decimal(item['executed_volume']):

                                # 주문관리정보 변경 처리
                                UPDATE_TRADE_INFO = """
                                                    UPDATE trade_mng 
                                                    SET 
                                                        ord_state = :ord_state,
                                                        executed_vol = :executed_vol, 
                                                        remaining_vol = :remaining_vol, 
                                                        chgr_id = :chgr_id, 
                                                        chg_date = :chg_date
                                                    WHERE id = :id
                                                    AND ord_state = 'wait'
                                                    """
                                db.execute(text(UPDATE_TRADE_INFO), {
                                        "ord_state": item['state'],
                                        "executed_vol": Decimal(item['executed_volume']),
                                        "remaining_vol": Decimal(item['remaining_volume']),
                                        "chgr_id": user_id,
                                        "chg_date": datetime.now(),
                                        "id": chk_ord['id']
                                        })
                                db.commit()
            
            elif trade_mng.market_name == 'BITHUMB':
                param = dict( uuid=order_status['uuid'] )

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
                try:
                    # Call API
                    response = requests.get(bithumb_api_url + '/v1/order', params=param, headers=headers).json()
                except Exception as err:
                    # handle exception
                    print(err)

                if response is not None:
                    if chk_ord['ord_no'] == response['uuid']:

                        order_param = {
                                "ord_dtm": datetime.fromisoformat(response['created_at']).strftime("%Y%m%d%H%M%S"),
                                "ord_no": response['uuid'],
                                "prd_nm": response['market'],
                                "ord_tp": '01' if response['side'] == 'bid' else '02',
                                "ord_state": response['state'],
                                "ord_price": response['price'],
                                "ord_vol": response['volume'],
                                "executed_vol": response['executed_volume'],
                                "remaining_vol": response['remaining_volume']
                            }
                
                        order_list.append(order_param)

                        if chk_ord['remaining_vol'] != Decimal(response['remaining_volume']) or chk_ord['executed_vol'] != Decimal(response['executed_volume']):

                            # 주문관리정보 변경 처리
                            UPDATE_TRADE_INFO = """
                                                UPDATE trade_mng 
                                                SET 
                                                    ord_state = :ord_state,
                                                    executed_vol = :executed_vol, 
                                                    remaining_vol = :remaining_vol, 
                                                    chgr_id = :chgr_id, 
                                                    chg_date = :chg_date
                                                WHERE id = :id
                                                AND ord_state = 'wait'
                                                """
                            db.execute(text(UPDATE_TRADE_INFO), {
                                    "ord_state": response['state'],
                                    "executed_vol": Decimal(response['executed_volume']),
                                    "remaining_vol": Decimal(response['remaining_volume']),
                                    "chgr_id": user_id,
                                    "chg_date": datetime.now(),
                                    "id": chk_ord['id']
                                    })
                            db.commit()

    return OrderResponse(order_list=order_list)

# 주문취소
@router.post("/cancel_order", response_model=TradeResponse)
def cancel_order(trade_mng: cancel_order, db: Session = Depends(config.get_db)):
    
    # 대기 상태 주문관리정보 조회
    SELECT_OPEN_ORDER_INFO = """
        SELECT A.cust_num, A.market_name, A.access_key, A.secret_key, B.id, B.prd_nm, B.ord_state, B.executed_vol, B.remaining_vol, B.ord_no
        FROM cust_mng A LEFT OUTER JOIN trade_mng B 
        ON A.cust_num = B.cust_num AND A.market_name = B.market_name
        WHERE A.cust_nm = :cust_nm AND A.market_name = :market_name AND B.ord_no = :ord_no AND B.ord_state IN ('wait' ,'watch')
    """
    chk_ord_list = db.execute(text(SELECT_OPEN_ORDER_INFO), {"cust_nm": trade_mng.cust_nm, "market_name": trade_mng.market_name, "ord_no": trade_mng.ord_no,}).mappings().all()

    ord_no = ""
    ord_state = ""

    for chk_ord in chk_ord_list :
        access_key = chk_ord['access_key']
        secret_key = chk_ord['secret_key']
        ord_no = chk_ord['ord_no']

        if trade_mng.market_name == 'UPBIT':
            params = {
                'uuid': ord_no,          # 주문 ID
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
            else:
                ord_state = "cancel success"

        elif trade_mng.market_name == 'BITHUMB':
            param = dict( uuid=ord_no )

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

            try:
                # 주문 취소 접수
                if len(requests.delete(bithumb_api_url + '/v1/order', params=param, headers=headers).json()) == 1:
                    ord_state = requests.delete(upbit_api_url + '/v1/order', params=param, headers=headers).json()['error']['message']
                else:
                    ord_state = "cancel success"
            except Exception as err:
                # handle exception
                print(err)
            
    return {"ord_no": ord_no, "ord_state": ord_state}    

# 종료된 주문 조회
@router.post("/close_order", response_model=OrderResponse)
def close_order(trade_mng: close_order, db: Session = Depends(config.get_db)):
    
    order_list = []

    # 1. 주문번호 존재 대상
    if trade_mng.ord_no != "":
        # 대기 상태 주문관리정보 조회
        SELECT_OPEN_ORDER_INFO = """
            SELECT A.cust_num, A.market_name, A.access_key, A.secret_key, B.id, B.prd_nm, B.ord_state, B.executed_vol, B.remaining_vol, B.ord_no
            FROM cust_mng A LEFT OUTER JOIN trade_mng B 
            ON A.cust_num = B.cust_num AND A.market_name = B.market_name
            WHERE A.cust_nm = :cust_nm AND A.market_name = :market_name AND B.ord_no = :ord_no AND B.ord_state IN ('wait' ,'watch')
        """
        chk_ord_list = db.execute(text(SELECT_OPEN_ORDER_INFO), {"cust_nm": trade_mng.cust_nm, "market_name": trade_mng.market_name, "ord_no": trade_mng.ord_no,}).mappings().all()

        for chk_ord in chk_ord_list :
            access_key = chk_ord['access_key']
            secret_key = chk_ord['secret_key']

            date_obj = datetime.strptime(trade_mng.start_dt, '%Y%m%d')
            datetime_with_time = datetime.combine(date_obj, datetime.strptime('00:00:00', '%H:%M:%S').time())
            start_dt = datetime_with_time.isoformat() + "+09:00"

            params = {
                'market': chk_ord['prd_nm'],        # 마켓 ID
                'states[]': ['done', 'cancel'],
                "start_time": start_dt,             # 조회시작일 이후 7일까지
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
            # 종료된 주문 조회
            result = requests.get(upbit_api_url + '/v1/orders/closed', params=params, headers=headers).json()

            if result is not None:
                for item in result:
                    if chk_ord['ord_no'] == item['uuid']:
                        order_param = {
                            "ord_dtm": datetime.fromisoformat(item['created_at']).strftime("%Y%m%d%H%M%S"),
                            "ord_no": item['uuid'],
                            "prd_nm": item['market'],
                            "ord_tp": '01' if item['side'] == 'bid' else '02',
                            "ord_state": item['state'],
                            "ord_price": item['price'],
                            "ord_vol": item['volume'],
                            "executed_vol": item['executed_volume'],
                            "remaining_vol": item['remaining_volume']
                        }
            
                        order_list.append(order_param)

                        # 주문관리정보 변경 처리
                        UPDATE_TRADE_INFO = """
                                            UPDATE trade_mng 
                                            SET 
                                                ord_state = :ord_state,
                                                executed_vol = :executed_vol, 
                                                remaining_vol = :remaining_vol, 
                                                chgr_id = :chgr_id, 
                                                chg_date = :chg_date
                                            WHERE id = :id
                                            AND ord_state = 'wait'
                                            """
                        db.execute(text(UPDATE_TRADE_INFO), {
                                "ord_state": item['state'],
                                "executed_vol": Decimal(item['executed_volume']),
                                "remaining_vol": Decimal(item['remaining_volume']),
                                "chgr_id": user_id,
                                "chg_date": datetime.now(),
                                "id": chk_ord['id']
                                })
                        db.commit()
    # 2. 주문번호 미존재 대상
    else:
        # 이전 주문 조회 위한 고객관리정보 조회
        SELECT_OPEN_ORDER_INFO = """
                SELECT A.cust_num, A.market_name, A.access_key, A.secret_key
                FROM cust_mng A
                WHERE A.cust_nm = :cust_nm AND A.market_name = :market_name
        """
        chk_ord_list = db.execute(text(SELECT_OPEN_ORDER_INFO), {"cust_nm": trade_mng.cust_nm, "market_name": trade_mng.market_name,}).mappings().all()

        for chk_ord in chk_ord_list :
            access_key = chk_ord['access_key']
            secret_key = chk_ord['secret_key']

            date_obj = datetime.strptime(trade_mng.start_dt, '%Y%m%d')
            datetime_with_time = datetime.combine(date_obj, datetime.strptime('00:00:00', '%H:%M:%S').time())
            start_dt = datetime_with_time.isoformat() + "+09:00"

            params = {
                'market': trade_mng.prd_nm,         # 마켓 ID
                'states[]': ['done', 'cancel'],
                "start_time": start_dt,             # 조회시작일 이후 7일까지
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
            # 종료된 주문 조회
            raw_order_list = requests.get(upbit_api_url + '/v1/orders/closed', params=params, headers=headers).json()

            for item in raw_order_list:
                order_param = {
                    "ord_dtm": datetime.fromisoformat(item['created_at']).strftime("%Y%m%d%H%M%S"),
                    "ord_no": item['uuid'],
                    "prd_nm": item['market'],
                    "ord_tp": '01' if item['side'] == 'bid' else '02',
                    "ord_state": item['state'],
                    "ord_price": item['price'],
                    "ord_vol": item['volume'],
                    "executed_vol": item['executed_volume'],
                    "remaining_vol": item['remaining_volume']
                }
    
                order_list.append(order_param)

                SELECT_TRADE_INFO = """
                        SELECT A.id
                        FROM trade_mng A
                        WHERE A.ord_no = :ord_no
                """
                chk_trade_list = db.execute(text(SELECT_TRADE_INFO), {"ord_no": item['uuid'],}).first()

                if chk_trade_list is not None:

                    # 주문관리정보 변경 처리
                    UPDATE_TRADE_INFO = """
                                        UPDATE trade_mng 
                                        SET 
                                            ord_state = :ord_state,
                                            executed_vol = :executed_vol, 
                                            remaining_vol = :remaining_vol, 
                                            chgr_id = :chgr_id, 
                                            chg_date = :chg_date
                                        WHERE id = :id
                                        AND ord_state = 'wait'
                                        """
                    db.execute(text(UPDATE_TRADE_INFO), {
                            "ord_state": item['state'],
                            "executed_vol": Decimal(item['executed_volume']),
                            "remaining_vol": Decimal(item['remaining_volume']),
                            "chgr_id": user_id,
                            "chg_date": datetime.now(),
                            "id": chk_trade_list[0]
                            })
                    db.commit()
        
    return OrderResponse(order_list=order_list)

def create_trade_plan(plan_list, db: Session = Depends(config.get_db)):

    try:
        for plan in plan_list:
            params = {
                "cust_nm": plan['cust_nm'], 
                "market_name": plan['market_name'], 
                "prd_nm": plan['prd_nm'], 
                "plan_tp": plan['plan_tp'],
                "plan_dtm": datetime.now().strftime('%Y%m%d%H%M%S'), 
                "price": plan["price"], 
                "volume": plan["volume"],
                "plan_price": plan['plan_price'],
                "plan_vol": plan['plan_vol'],
                "plan_amt": plan['plan_amt'],
                "regist_price": plan['regist_price'],
                "support_price": plan['support_price'],
                "regr_id": user_id,
                "reg_date": datetime.now(),
                "chgr_id": user_id,
                "chg_date": datetime.now()
            }

            # 매매예정이력정보 존재여부 체크
            TRADE_PLAN_HIST = text("""
                SELECT 1
                FROM trade_plan_hist
                WHERE cust_nm = :cust_nm 
                AND market_name = :market_name
                AND prd_nm = :prd_nm 
                AND plan_tp = :plan_tp
                AND plan_execute = 'N'
                AND plan_price = :plan_price
                AND plan_vol = :plan_vol
                AND plan_amt = :plan_amt
                AND regist_price = :regist_price
                AND support_price = :support_price
            """)
            
            chk_trade_plan_hist = db.execute(TRADE_PLAN_HIST,params).first()

            if chk_trade_plan_hist is None:
            
                # 기존 데이터 백업
                INSERT_TRADE_PLAN_HIST = text("""
                    INSERT INTO trade_plan_hist (
                        cust_nm, market_name, plan_dtm, plan_execute, prd_nm, price, volume, 
                        plan_tp, plan_price, plan_vol, plan_amt, regist_price, support_price, 
                        regr_id, reg_date, chgr_id, chg_date
                    )
                    SELECT cust_nm, market_name, plan_dtm, plan_execute, prd_nm, price, volume, 
                        plan_tp, plan_price, plan_vol, plan_amt, regist_price, support_price, 
                        regr_id, reg_date, chgr_id, chg_date
                    FROM trade_plan
                    WHERE cust_nm = :cust_nm 
                    AND market_name = :market_name
                    AND prd_nm = :prd_nm 
                    AND plan_tp = :plan_tp
                    AND plan_execute = 'N'                     
                """)
                
                result1 = db.execute(INSERT_TRADE_PLAN_HIST, params)

                # 백업이 성공한 경우에만 삭제
                if result1.rowcount > 0:
                    DELETE_TRADE_PLAN = text("""
                        DELETE FROM trade_plan
                        WHERE cust_nm = :cust_nm 
                        AND market_name = :market_name
                        AND prd_nm = :prd_nm 
                        AND plan_tp = :plan_tp
                        AND plan_execute = 'N'
                    """)
                    db.execute(DELETE_TRADE_PLAN, params)

                # 새로운 데이터 삽입 (중복 방지 포함)
                INSERT_TRADE_PLAN = text("""
                    INSERT INTO trade_plan (
                        cust_nm, market_name, plan_dtm, plan_execute, prd_nm, price, volume, 
                        plan_tp, plan_price, plan_vol, plan_amt, regist_price, support_price, 
                        regr_id, reg_date, chgr_id, chg_date
                    )
                    SELECT :cust_nm, :market_name, :plan_dtm, 'N', :prd_nm, :price, :volume, 
                        :plan_tp, :plan_price, :plan_vol, :plan_amt, :regist_price, 
                        :support_price, :regr_id, :reg_date, :chgr_id, :chg_date
                    WHERE NOT EXISTS (
                        SELECT 1 FROM trade_plan 
                        WHERE cust_nm = :cust_nm 
                        AND market_name = :market_name
                        AND prd_nm = :prd_nm 
                        AND plan_tp = :plan_tp
                        AND plan_execute = 'N'
                    );
                """)
                db.execute(INSERT_TRADE_PLAN, params)
            
        db.commit()
            
    except Exception as e:
        db.rollback()
        raise e

def regist_trade_plan_hist(cust_num, cust_nm, market_name, prd_nm, db: Session = Depends(config.get_db)):
    
    try:
        # 잔고정보 미존재 대상 매매처리된 매매예정정보 백업 처리
        EX_INSERT_TRADE_PLAN_HIST = text("""
            INSERT INTO trade_plan_hist (
                cust_nm, market_name, plan_dtm, plan_execute, prd_nm, price, volume, 
                plan_tp, plan_price, plan_vol, plan_amt, regist_price, support_price, 
                regr_id, reg_date, chgr_id, chg_date
            )
            SELECT cust_nm, market_name, plan_dtm, plan_execute, prd_nm, price, volume, 
                plan_tp, plan_price, plan_vol, plan_amt, regist_price, support_price, 
                regr_id, reg_date, chgr_id, chg_date
            FROM trade_plan
            WHERE cust_nm = :cust_nm 
            AND market_name = :market_name
            AND prd_nm = :prd_nm 
            AND plan_execute = 'Y'
            AND NOT EXISTS (
                SELECT 1
                FROM balance_info 
                WHERE cust_num = :cust_num AND market_name = :market_name AND prd_nm = :prd_nm
            )                      
        """)
        
        result2 = db.execute(EX_INSERT_TRADE_PLAN_HIST, {"cust_num": cust_num, "cust_nm": cust_nm, "market_name": market_name, "prd_nm": prd_nm,})

        # 백업이 성공한 경우에만 삭제
        if result2.rowcount > 0:
            EX_DELETE_TRADE_PLAN = text("""
                DELETE FROM trade_plan
                WHERE cust_nm = :cust_nm 
                AND market_name = :market_name
                AND prd_nm = :prd_nm 
                AND plan_execute = 'Y'
                AND NOT EXISTS (
                    SELECT 1
                    FROM balance_info 
                    WHERE cust_num = :cust_num AND market_name = :market_name AND prd_nm = :prd_nm
                )                      
            """)
            db.execute(EX_DELETE_TRADE_PLAN, {"cust_num": cust_num, "cust_nm": cust_nm, "market_name": market_name, "prd_nm": prd_nm,})
            
        db.commit()
            
    except Exception as e:
        db.rollback()
        raise e            

def balance(access_key, secret_key, market_name, prd_nm: Optional[str] = None,):

    api_url = ''
    try:
        if market_name == 'UPBIT':
            api_url = upbit_api_url
            payload = {
                'access_key': access_key,
                'nonce': str(uuid.uuid4()),
            }
        elif market_name == 'BITHUMB':   
            api_url = bithumb_api_url
            payload = {
                'access_key': access_key,
                'nonce': str(uuid.uuid4()),
                'timestamp': round(time.time() * 1000)
            }

        # 잔고 조회
        jwt_token = jwt.encode(payload, secret_key)
        authorization = 'Bearer {}'.format(jwt_token)
        headers = {
        'Authorization': authorization,
        }

        res = requests.get(api_url + '/v1/accounts',headers=headers)
        accounts = res.json()
        
    except Exception as e:
        print(f"[잔고 조회 예외] 오류 발생: {e}")
        accounts = []  # 또는 None 등, 이후 구문에서 사용할 수 있도록 기본값 설정    

    currency_list = list()
    cnt_currency = 0
    price = 0
    volume = 0
    amt = 0
    trade_price = 0
    current_amt = 0
    loss_profit_amt = 0
    loss_profit_rate = 0

    # 상품명 존재(매도주문)인 경우
    if prd_nm is not None:
        for item in accounts:
            if item['currency'] == prd_nm:
                price = float(item['avg_buy_price'])  # 평균단가    
                volume = float(item['balance']) + float(item['locked'])    # 보유수량 = 주문가능 수량 + 주문묶여있는 수량
                amt = int(price * volume)  # 보유금액  

                params = {
                    "markets": "KRW-"+item['currency']
                }

                trade_price = 0
                current_amt = 0
                loss_profit_amt = 0
                loss_profit_rate = 0

                try:
                    # 현재가 정보
                    res = requests.get(api_url + "/v1/ticker", params=params).json()

                    if isinstance(res, dict) and 'error' in res:
                        # 에러 메시지가 반환된 경우
                        error_name = res['error'].get('name', 'Unknown')
                        error_message = res['error'].get('message', 'Unknown')
                        print(f"[Ticker 조회 오류] {error_name}: {error_message}")

                except Exception as e:
                    print(f"[Ticker 조회 예외] 오류 발생: {e}")
                    res = None 
                
                if res:  
                    trade_price = float(res[0]['trade_price'])
                    
                    # 현재평가금액
                    current_amt = int(trade_price * volume)
                    # 손실수익금
                    loss_profit_amt = current_amt - amt
                    # 손실수익률
                    loss_profit_rate = ((100 - Decimal(trade_price / price) * 100) * -1).quantize(Decimal('0.01'), rounding=ROUND_DOWN)

                    currency_param = {
                        "name" : item['currency'],
                        "price" : price,
                        "volume" : volume,
                        "amt" : amt,
                        "locked_volume" : float(item['locked']),
                        "locked_amt" : int(price*float(item['locked'])),
                        "trade_price" : trade_price,
                        "current_amt" : current_amt,
                        "loss_profit_amt" : loss_profit_amt,
                        "loss_profit_rate" : loss_profit_rate,
                    }
                    cnt_currency += 1
                    currency_list.append(currency_param)

    else:
        for i, item in enumerate(accounts):
            
            price = float(item['avg_buy_price'])  # 평균단가    
            volume = float(item['balance']) + float(item['locked'])    # 보유수량 = 주문가능 수량 + 주문묶여있는 수량
            amt = int(price * volume)  # 보유금액  

            if item['currency'] not in ["P", "KRW"]:
                params = {
                    "markets": "KRW-"+item['currency']
                }

                trade_price = 0
                current_amt = 0
                loss_profit_amt = 0
                loss_profit_rate = 0

                try:
                    # 현재가 정보
                    res = requests.get(api_url + "/v1/ticker", params=params).json()

                    if isinstance(res, dict) and 'error' in res:
                        # 에러 메시지가 반환된 경우
                        error_name = res['error'].get('name', 'Unknown')
                        error_message = res['error'].get('message', 'Unknown')
                        print(f"[Ticker 조회 오류] {error_name}: {error_message}")
                        continue

                except Exception as e:
                    print(f"[Ticker 조회 예외] 오류 발생: {e}")
                    res = None 
                
                if res:  
                    trade_price = float(res[0]['trade_price'])
                    trade_volume = float(res[0]['acc_trade_volume'])
                    
                    if trade_price == 0:
                        continue
                    
                    result = candle_info("KRW-"+item['currency'], market_name, api_url)
                    # 전일저가 금일종가 이탈하는 경우
                    if trade_price < result[0]['low_price']:
                        print("name : ",item['currency'], "현재가 : ",trade_price, "전일 저가 이탈 : ",result[0]['low_price'])
                        # 거래량이 전일보다 많은 경우
                        if trade_volume > result[0]['trade_volume']:
                            print("name : ",item['currency'],"거래량 : ",trade_volume, "전일 거래량 : ",result[0]['trade_volume'])

                    is_breakdown = candle_minutes_info("KRW-"+item['currency'], market_name, api_url, "15")
                    if is_breakdown:
                        print("name : ", item['currency'], " 의 이전 분봉의 저가를 이탈했습니다.")
                            
                    # 현재평가금액
                    current_amt = int(trade_price * volume)
                    # 손실수익금
                    loss_profit_amt = current_amt - amt
                    # 손실수익률
                    loss_profit_rate = ((100 - Decimal(trade_price / price) * 100) * -1).quantize(Decimal('0.01'), rounding=ROUND_DOWN)

                    currency_param = {
                        "name" : item['currency'],
                        "price" : price,
                        "volume" : volume,
                        "amt" : amt,
                        "locked_volume" : float(item['locked']),
                        "locked_amt" : int(price*float(item['locked'])),
                        "trade_price" : trade_price,
                        "current_amt" : current_amt,
                        "loss_profit_amt" : loss_profit_amt,
                        "loss_profit_rate" : loss_profit_rate,
                    }
                    cnt_currency += 1
                    currency_list.append(currency_param)
            else:
                currency_param = {
                    "name" : item['currency'],
                    "price" : 0,
                    "volume" : 0,
                    "amt" : int(volume),
                    "locked_volume" : 0,
                    "locked_amt" : int(float(item['locked'])),
                    "trade_price" : 0,
                    "current_amt" : 0,
                    "loss_profit_amt" : 0,
                    "loss_profit_rate" : 0,
                }
                cnt_currency += 1
                currency_list.append(currency_param)        
            time.sleep(0.1)

    return currency_list

def candle_info(market, market_name, api_url):

    today = datetime.now()
    datetime_with_time = datetime.combine(today, datetime.strptime('00:00:00', '%H:%M:%S').time())
    start_dt = datetime_with_time.isoformat() + "+09:00"

    # 현재일 기준 전일봉 1개를 요청
    if market_name == 'UPBIT':
        url = api_url + "/v1/candles/days"
        params = {  
            'market': market,  
            'count': 1,
            'to': start_dt
        } 
        headers = {"accept": "application/json"}
        response = requests.get(url, params=params, headers=headers).json()

    elif market_name == 'BITHUMB':
        url = api_url + "/v1/candles/days?market=" + market + "&count=1"
        headers = {"accept": "application/json"}
        response = requests.get(url, headers=headers).json()
    
    candle_list = list()

    for item in response:
        name = item['market']
        trade_price = float(item['trade_price'])
        low_price = float(item['low_price'])
        high_price = float(item['high_price'])
        trade_volume = float(item['candle_acc_trade_volume'])
        
        candle_param = {
            "name" : name,
            "trade_price" : trade_price,
            "high_price" : high_price,
            "low_price" : low_price,
            "trade_volume" : trade_volume,
        }
        candle_list.append(candle_param)
    
    return candle_list

def candle_minutes_info(market, market_name, api_url, in_minutes):

    # UTC 시간을 사용
    now = datetime.now(timezone.utc).isoformat()
    is_breakdown = False

    if market_name.upper() == 'UPBIT':
        url = f"{api_url}/v1/candles/minutes/{in_minutes}"
        
        params = {  
            'market': market,  
            'count': 2,  # 최근 2개 분봉을 가져옴
            'to': now  
        } 
        headers = {"accept": "application/json"}

        try:
            response = requests.get(url, params=params, headers=headers)
            response.raise_for_status()  # HTTP 오류 처리
            data = response.json()
        except requests.RequestException as e:
            print(f"Error fetching data from Upbit: {e}")
            return []

        if len(data) < 2:
            print("Not enough candle data available.")
            return []

        # 최근 분봉 (현재 캔들)과 이전 분봉
        current_candle = data[0]
        previous_candle = data[1]

        # 현재 분봉 종가가 이전 분봉 저가를 이탈했는지와 이전 분봉의 거래량보다 현재 분봉의 거래량이 큰 경우 체크
        is_breakdown = current_candle["trade_price"] < previous_candle["low_price"] and current_candle["candle_acc_trade_volume"] > previous_candle["candle_acc_trade_volume"]

    elif market_name.upper() == 'BITHUMB':
        url = f"{api_url}/v1/candles/minutes/{in_minutes}?market={market}&count=2&to={now}"
        headers = {"accept": "application/json"}
        
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()  # HTTP 오류 처리
            data = response.json()
        except requests.RequestException as e:
            print(f"Error fetching data from Upbit: {e}")
            return []

        if len(data) < 2:
            print("Not enough candle data available.")
            return []
        
        # 최근 분봉 (현재 캔들)과 이전 분봉
        current_candle = data[0]
        previous_candle = data[1]

        # 현재 분봉 종가가 이전 분봉 저가를 이탈했는지와 이전 분봉의 거래량보다 현재 분봉의 거래량이 큰 경우 체크
        is_breakdown = current_candle["trade_price"] < previous_candle["low_price"] and current_candle["candle_acc_trade_volume"] > previous_candle["candle_acc_trade_volume"]
    
    return is_breakdown

def place_order(access_key, secret_key, market, side, volume, price, ord_type="limit"):
    params= {
        'market': market,       # 마켓 ID
        'side': side,           # bid : 매수, ask : 매도
        'ord_type': ord_type,   # limit : 지정가 주문, price : 시장가 매수, market : 시장가 매도, best : 최유리 주문
        'price': price,         # 호가 매수
        'volume': volume        # 주문량
    }
    print(params)
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

    # 주문 전송
    res = requests.post(upbit_api_url + '/v1/orders', json=params, headers=headers)
    print("result : ", res.json())
    return res.json()

def bithumb_order(access_key, secret_key, market, side, volume, price, ord_type="limit"):
    requestBody = dict( market=market, side=side, volume=volume, price=price, ord_type=ord_type)

    # Generate access token
    query = urlencode(requestBody).encode()
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
        'Authorization': authorization_token,
        'Content-Type': 'application/json'
    }

    try:
        # Call API
        response = requests.post(bithumb_api_url + '/v1/orders', data=json.dumps(requestBody), headers=headers)
        # handle to success or fail
        print(response.status_code)
        print(response.json())
    except Exception as err:
        # handle exception
        print(err)
    
    return response.json()    

def get_order(access_key, secret_key, order_uuid):
    params = {"uuid": order_uuid}
    print("order_uuid : ",order_uuid)
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
    # 주문 조회
    response = requests.get(upbit_api_url + "/v1/order", params=params, headers=headers)
    print("response : ", response.json())
    return response.json()

def bithumb_get_order(access_key, secret_key, order_uuid):
    param = dict( uuid=order_uuid )

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

    try:
        # Call API
        response = requests.get(bithumb_api_url + '/v1/order', params=param, headers=headers)
        # handle to success or fail
        print(response.status_code)
        print(response.json())
    except Exception as err:
        # handle exception
        print(err)

    return response.json()    

# 고점과 저점 계산 함수
def calculate_peaks_and_troughs(data):
    highs = []
    lows = []

    for i in range(1, len(data) - 1):
        prev_close = data['close'].iloc[i - 1]
        curr_close = data['close'].iloc[i]
        next_close = data['close'].iloc[i + 1]

        # 고점: 상승 후 하락
        if curr_close > prev_close and curr_close > next_close:
            highs.append(curr_close)
        else:
            highs.append(None)

        # 저점: 하락 후 상승
        if curr_close < prev_close and curr_close < next_close:
            lows.append(curr_close)
        else:
            lows.append(None)

    # 첫 번째와 마지막 값은 None 처리
    highs.insert(0, None)
    lows.insert(0, None)
    highs.append(None)
    lows.append(None)

    data['High Points'] = highs
    data['Low Points'] = lows
    return data

# 추세 판단 함수
def determine_trends(data):
    trend = []
    last_high = None
    last_low = None

    for i in range(len(data)):
        curr_close = data['close'].iloc[i]
        high_point = data['High Points'].iloc[i]
        low_point = data['Low Points'].iloc[i]

        if pd.notna(high_point):  # 고점 형성
            last_high = high_point

        if pd.notna(low_point):  # 저점 형성
            last_low = low_point

        # 상승 추세: 고점 재돌파
        if last_high and curr_close > last_high:
            trend.append('Uptrend')

        # 하락 추세: 저점 재이탈
        elif last_low and curr_close < last_low:
            trend.append('Downtrend')

        else:
            trend.append('Sideways')

    data['Trend'] = trend
    return data