from sqlalchemy import Column, String, DateTime, Integer, Numeric
from sqlalchemy.ext.declarative import declarative_base
from pydantic import BaseModel, validator, Field
import re
from decimal import Decimal
from typing import Optional

Base = declarative_base()


class CustMng(Base):
    __tablename__ = "cust_mng"

    id = Column(Integer, primary_key=True)
    cust_num = Column(String(10))
    cust_nm = Column(String(100))
    market_name = Column(String(20))
    acct_no = Column(String(20))
    access_key = Column(String(100))
    secret_key = Column(String(200))
    access_token = Column(String(400))
    token_publ_date = Column(String(14))
    regr_id = Column(String(50))
    reg_date = Column(DateTime(True))
    chgr_id = Column(String(50))
    chg_date = Column(DateTime(True))

class CustCreate(BaseModel):
    # cust_num: Optional[str] = Field(None, description="고객번호")
    cust_nm: Optional[str] = Field(None, description="고객명")
    market_name: Optional[str] = Field(None, description="거래소명")
    acct_no: Optional[str] = Field(None, description="계좌번호")
    access_key: Optional[str] = Field(None, description="접근키")
    secret_key: Optional[str] = Field(None, description="암호키")
    # access_token: Optional[str] = Field(None, description="접근토큰")
    # token_publ_date: Optional[str] = Field(None, description="토큰발행일시")

class CustInfoResponse(BaseModel):
    cust_num: str
    cust_nm: str