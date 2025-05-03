from config.db import SessionLocal 
from models.trade_mng import account_list as AccountListRequest
from routers.trade_mng import account_list

def format_number(value):
    try:
        return f"{float(value):,.2f}" if isinstance(value, float) else f"{int(value):,}"
    except:
        return str(value)

def get_balance(cust_nm: str, market_name: str) -> str:
    db = SessionLocal()
    try:
        req_data = AccountListRequest(cust_nm=cust_nm, market_name=market_name)
        result = account_list(req_data, db)

        text_lines = []
        for item in result["balance_list"]:
            text_lines.append(
                f"*{item['name']}*: 보유단가 {format_number(item['price'])}, 보유금액 {format_number(item['amt'])}원, "
                f"보유수량 {format_number(item['volume'])}, 매도진행수량 {format_number(item['locked_volume'])}, "
                f"현재가 {format_number(item['trade_price'])}, 평가금액 {format_number(item['current_amt'])}원, "
                f"손익금액 {format_number(item['loss_profit_amt'])}원, 손익률 {item['loss_profit_rate']}%"
            )

        return "\n".join(text_lines) if text_lines else "잔고가 없습니다."
    except Exception as e:
        return f"잔고조회 실패: {e}"
    finally:
        db.close()