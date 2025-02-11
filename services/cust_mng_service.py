from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from passlib.context import CryptContext
from models.cust_mng import CustMng
from sqlalchemy import text
from config.db import get_db

def create_cust(db: Session, cust_nm: str, market_name:str, acct_no:str, access_key: str, secret_key: str):

    cust_info = get_cust_info(db, market_name, acct_no)
    if cust_info:
        raise ValueError("Cust Info already registered")

    cust_num = get_cust_num(db)

    user_id = "FASTAPI"

    INSERT_CUST_INFO = """INSERT INTO cust_mng (
            cust_num, 
            cust_nm, 
            market_name, 
            acct_no, 
            access_key, 
            secret_key, 
            regr_id, 
            reg_date, 
            chgr_id, 
            chg_date)
        VALUES (
            :cust_num, 
            :cust_nm, 
            :market_name,
            :acct_no,
            :access_key,
            :secret_key,
            :regr_id,
            :reg_date,
            :chgr_id,
            :chg_date)
    """
    db.execute(text(INSERT_CUST_INFO), {
        "cust_num": cust_num, 
        "cust_nm": cust_nm, 
        "market_name": market_name, 
        "acct_no": acct_no, 
        "access_key": access_key,
        "secret_key": secret_key,
        "regr_id": user_id,
        "reg_date": datetime.now(),
        "chgr_id": user_id,
        "chg_date": datetime.now()
        })
    db.commit()
    db_cust_info = CustMng(cust_num=cust_num, cust_nm=cust_nm)

    return db_cust_info

def get_cust_info(db: Session, market_name: str, acct_no: str):

    SELECT_CUST_INFO = text("SELECT cust_num FROM cust_mng WHERE market_name = :market_name AND acct_no = :acct_no")
    result = db.execute(SELECT_CUST_INFO, {"market_name": market_name, "acct_no": acct_no}).fetchone()

    return result

def get_cust_num(db: Session):

    SEQ_CUST_NUM = text("SELECT nextval('cust_mng_cust_num_seq')")
    create_cust_num = db.execute(SEQ_CUST_NUM).mappings().all()

    result = str(create_cust_num[0]['nextval'])

    return result

def get_cust_info_by_cust_nm(db: Session, cust_nm: str, market_name: str):

    SELECT_CUST_INFO = text("SELECT cust_num, cust_nm, market_name, acct_no, access_key, secret_key, access_token, token_publ_date FROM cust_mng WHERE cust_nm = :cust_nm AND market_name = :market_name")
    result = db.execute(SELECT_CUST_INFO, {"cust_nm": cust_nm, "market_name": market_name,}).fetchone()

    return result    