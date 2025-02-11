from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from config import db as config
from models.cust_mng import CustCreate, CustInfoResponse
from services import cust_mng_service

router = APIRouter()

@router.post("/custreg", response_model=CustInfoResponse)
def custreg(cust_mng: CustCreate, db: Session = Depends(config.get_db)):
    try:
        db_cust_info = cust_mng_service.create_cust(db, cust_mng.cust_nm, cust_mng.market_name, cust_mng.acct_no, cust_mng.access_key, cust_mng.secret_key)
        return {"cust_num": db_cust_info.cust_num, "cust_nm": db_cust_info.cust_nm}
    except ValueError:
        raise HTTPException(status_code=400, detail="Cust Info Already Registered")
    
