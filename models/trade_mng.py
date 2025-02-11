from sqlalchemy import Column, String, DateTime, Integer, Numeric
from sqlalchemy.ext.declarative import declarative_base
from pydantic import BaseModel, validator, Field
import re
from decimal import Decimal
from typing import List, Optional

Base = declarative_base()


class TradeMng(Base):
    __tablename__ = "trade_mng"

    id = Column(Integer, primary_key=True)
    cust_num = Column(String(10))
    market_name = Column(String(20))
    ord_dtm = Column(String(14))
    ord_no = Column(String(50))
    orgn_ord_no = Column(String(50))
    prd_nm = Column(String(50))
    ord_tp = Column(String(20))
    ord_state = Column(String(20))
    ord_count = Column(Integer)
    ord_expect_totamt = Column(Integer)
    ord_price = Column(Numeric(20, 8))
    ord_vol = Column(Numeric(20, 8))
    ord_amt = Column(Integer)
    cut_price = Column(Numeric(20, 8))
    cut_rate = Column(Numeric(6, 2))
    cut_amt = Column(Integer)
    goal_price = Column(Numeric(20, 8))
    goal_rate = Column(Numeric(6, 2))
    goal_amt = Column(Integer)
    margin_vol = Column(Numeric(20, 8))
    executed_vol = Column(Numeric(20, 8))
    remaining_vol = Column(Numeric(20, 8))
    regr_id = Column(String(50))
    reg_date = Column(DateTime(True))
    chgr_id = Column(String(50))
    chg_date = Column(DateTime(True))

class BalanceInfo(BaseModel):
    name: Optional[str] = Field(None, description="상품명")
    price: Optional[Decimal] = Field(None, description="단가")
    volume: Optional[Decimal] = Field(None, description="수량")
    amt: Optional[int] = Field(None, description="금액")
    locked_volume: Optional[Decimal] = Field(None, description="예약수량")
    locked_amt: Optional[int] = Field(None, description="예약금액")
    trade_price: Optional[Decimal] = Field(None, description="현재가")
    current_amt: Optional[int] = Field(None, description="현재평가금액")
    loss_profit_amt: Optional[int] = Field(None, description="손실수익금")
    loss_profit_rate: Optional[Decimal] = Field(None, description="손실수익률")

class dividion_sell(BaseModel):
    cust_nm: Optional[str] = Field(None, description="고객명")
    market_name: Optional[str] = Field(None, description="거래소명")
    prd_nm: Optional[str] = Field(None, description="상품명")
    ord_tp: Optional[str] = Field(None, description="주문유형")
    ord_count: Optional[int] = Field(None, description="주문회차")
    ord_price: Optional[Decimal] = Field(None, description="주문가")
    ord_vol: Optional[Decimal] = Field(None, description="주문량")
    cut_price: Optional[Decimal] = Field(None, description="손절가")
    goal_price: Optional[Decimal] = Field(None, description="목표가")

class dividion_buy(BaseModel):
    cust_nm: Optional[str] = Field(None, description="고객명")
    market_name: Optional[str] = Field(None, description="거래소명")
    prd_nm: Optional[str] = Field(None, description="상품명")
    ord_tp: Optional[str] = Field(None, description="주문유형")
    ord_count: Optional[int] = Field(None, description="주문회차")
    ord_expect_totamt: Optional[int] = Field(None, description="주문예정총액")
    ord_price: Optional[Decimal] = Field(None, description="주문가")
    cut_price: Optional[Decimal] = Field(None, description="손절가")
    goal_price: Optional[Decimal] = Field(None, description="목표가")

class open_order(BaseModel):
    cust_nm: Optional[str] = Field(None, description="고객명")
    market_name: Optional[str] = Field(None, description="거래소명")
    prd_nm: Optional[str] = Field(None, description="상품명")

class cancel_order(BaseModel):
    cust_nm: Optional[str] = Field(None, description="고객명")
    market_name: Optional[str] = Field(None, description="거래소명")
    ord_no: Optional[str] = Field(None, description="주문번호")

class close_order(BaseModel):
    cust_nm: Optional[str] = Field(None, description="고객명")
    market_name: Optional[str] = Field(None, description="거래소명")
    prd_nm: Optional[str] = Field(None, description="상품명")
    ord_no: Optional[str] = Field(None, description="주문번호")
    start_dt: Optional[str] = Field(None, description="조회시작일")

class account_list(BaseModel):
    cust_nm: Optional[str] = Field(None, description="고객명")
    market_name: Optional[str] = Field(None, description="거래소명")

class TradeResponse(BaseModel):
    ord_no: str
    ord_state: str

class SellResponse(BaseModel):
    balance_list: Optional[List[BalanceInfo]] = Field(None, description="잔고정보")

class BalanceResponse(BaseModel):
    balance_list: Optional[List[BalanceInfo]] = Field(None, description="잔고정보")

class OrderInfo(BaseModel):
    ord_dtm : Optional[str] = Field(None, description="주문일시")
    ord_no: Optional[str] = Field(None, description="주문번호")
    prd_nm: Optional[str] = Field(None, description="상품명")
    ord_tp: Optional[str] = Field(None, description="주문유형")
    ord_state: Optional[str] = Field(None, description="주문상태")
    ord_price: Optional[str] = Field(None, description="주문가")
    ord_vol: Optional[str] = Field(None, description="주문수량")
    executed_vol: Optional[str] = Field(None, description="체결수량")
    remaining_vol: Optional[str] = Field(None, description="주문잔량")

class OrderResponse(BaseModel):
    order_list: Optional[List[OrderInfo]] = Field(None, description="주문정보")

class trade_plan(BaseModel):
    cust_nm: Optional[str] = Field(None, description="고객명")
    market_name: Optional[str] = Field(None, description="거래소명")
    prd_nm: Optional[str] = Field(None, description="상품명")
    plan_tp: Optional[str] = Field(None, description="매매예정구분")
    plan_price: Optional[Decimal] = Field(None, description="매매예정가")
    plan_tot_amt: Optional[int] = Field(None, description="매매예정금액")
    support_price: Optional[Decimal] = Field(None, description="지지가")
    regist_price: Optional[Decimal] = Field(None, description="저항가")