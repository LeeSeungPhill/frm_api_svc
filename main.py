from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import asyncio
import json
import requests
from routers.trade_proc import get_balance, buy_proc, sell_proc, get_order_open, order_update, order_cancel, get_order_close, get_interest_list, interest_update, get_holding_prd_list, get_holding_prices, holding_update, get_cust_list, get_ticker, calc_buy_plan, get_sell_holding_list
from typing import List, Tuple, Union, Optional
import re
import base64
from datetime import datetime
from urllib.parse import quote
# from routers import auth as auth_router
from routers import cust_mng as cust_mng_router
from routers import trade_mng as trade_mng_router
# from routers import bodo_mng as bodo_mng_router
import click
import subprocess
from montecarlo import montecarlo as montecarlo_

app = FastAPI()

# app.include_router(auth_router.router, prefix="/api/auth", tags=["auth"])
app.include_router(cust_mng_router.router, prefix="/api/cust_mng", tags=["cust_mng"])
app.include_router(trade_mng_router.router, prefix="/api/trade_mng", tags=["trade_mng"])
# app.include_router(bodo_mng_router.router, prefix="/api/bodo_mng", tags=["bodo_mng"])

MAX_BLOCKS = 50
MAX_VALUE_LENGTH = 2000
MAX_TEXT_LENGTH = 3000

@app.get("/")
def read_root():
    return {"message": "Hello, World!"}

def encode_value(payload: dict) -> str:
    """
    딕셔너리를 Base64로 인코딩된 문자열로 변환.
    """
    json_str = json.dumps(payload)
    return base64.urlsafe_b64encode(json_str.encode()).decode()

def decode_value(encoded_str: str) -> dict:
    try:
        decoded_bytes = base64.urlsafe_b64decode(encoded_str.encode())
        return json.loads(decoded_bytes.decode())
    except Exception as e:
        print(f"[decode_value] 디코딩 실패: {e}")
        return {}

def parse_optional_price(value: Optional[str], label: str) -> Optional[float]:
    """
    비어있으면 None, 값이 있으면 유효성 검사 후 float으로 변환.
    """
    if not value:
        return None
    if not re.fullmatch(r"\d+(\.\d{1,5})?", value):
        raise ValueError(f"{label}는 0 이상의 숫자이며 소숫점 5자리까지만 입력 가능합니다.")
    price = float(value)
    if price < 0:
        raise ValueError(f"{label}는 0 이상의 숫자여야 합니다.")
    return price

def format_price_input(value: Optional[float]) -> str:
    """
    입력항목의 초기값으로 표시하기 위해 소수점 5자리까지 표현하고 불필요한 0은 제거.
    """
    if value is None:
        return ""
    text_value = f"{float(value):.5f}".rstrip("0").rstrip(".")
    return text_value if text_value else "0"

def build_holding_update_blocks(
    market_name: str,
    cust_nm: str,
    holding_prd_list: list,
    selected_prd_nm: Optional[str] = None,
    prices: Optional[dict] = None,
    trading_plan_option: Optional[dict] = None
) -> list:
    """
    보유종목 수정 화면 블록 생성. 상품명 선택시 기존 이탈가/수행가/최종이탈가를 초기값으로 표시.
    """
    ctx_value = encode_value({"market_name": market_name, "cust_nm": cust_nm})
    value = json.dumps({"market_name": market_name, "cust_nm": cust_nm})
    prices = prices or {}
    price_block_suffix = f"|{selected_prd_nm}" if selected_prd_nm else ""

    def prd_nm_label(prd_nm: str) -> str:
        return prd_nm.split('-')[-1] if '-' in prd_nm else prd_nm

    prd_nm_element = {
        "type": "static_select",
        "action_id": "input_prd_nm",
        "placeholder": {
            "type": "plain_text",
            "text": "보유종목을 선택해주세요"
        },
        "options": [
            {
                "text": { "type": "plain_text", "text": prd_nm_label(prd_nm) },
                "value": prd_nm
            }
            for prd_nm in holding_prd_list
        ]
    }
    if selected_prd_nm:
        prd_nm_element["initial_option"] = {
            "text": { "type": "plain_text", "text": prd_nm_label(selected_prd_nm) },
            "value": selected_prd_nm
        }

    def price_element(action_id: str, placeholder: str, price_key: str) -> dict:
        element = {
            "type": "plain_text_input",
            "action_id": action_id,
            "placeholder": {
                "type": "plain_text",
                "text": placeholder
            }
        }
        if prices.get(price_key) is not None:
            element["initial_value"] = format_price_input(prices.get(price_key))
        return element

    trading_plan_element = {
        "type": "static_select",
        "action_id": "input_trading_plan",
        "placeholder": {
            "type": "plain_text",
            "text": "매매계획을 선택해주세요"
        },
        "options": [
            { "text": { "type": "plain_text", "text": "홀딩" }, "value": "h" },
            { "text": { "type": "plain_text", "text": "투자" }, "value": "i" },
            { "text": { "type": "plain_text", "text": "일반" }, "value": "NULL" }
        ]
    }
    if trading_plan_option:
        trading_plan_element["initial_option"] = trading_plan_option

    return [
        {
            "type": "input",
            "block_id": f"prd_nm_input_block|{ctx_value}",
            "dispatch_action": True,
            "element": prd_nm_element,
            "label": { "type": "plain_text", "text": "상품명" }
        },
        {
            "type": "input",
            "block_id": f"stop_price_input_block{price_block_suffix}",
            "optional": True,
            "element": price_element("input_stop_price", "이탈가를 입력해주세요 (선택)", "stop_price"),
            "label": { "type": "plain_text", "text": "이탈가" }
        },
        {
            "type": "input",
            "block_id": f"action_price_input_block{price_block_suffix}",
            "optional": True,
            "element": price_element("input_action_price", "수행가를 입력해주세요 (선택)", "action_price"),
            "label": { "type": "plain_text", "text": "수행가" }
        },
        {
            "type": "input",
            "block_id": f"exit_price_input_block{price_block_suffix}",
            "optional": True,
            "element": price_element("input_exit_price", "최종이탈가를 입력해주세요 (선택)", "exit_price"),
            "label": { "type": "plain_text", "text": "최종이탈가" }
        },
        {
            "type": "input",
            "block_id": "trading_plan_input_block",
            "element": trading_plan_element,
            "label": { "type": "plain_text", "text": "매매계획" }
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "보유종목 수정",
                        "emoji": True
                    },
                    "value": value,
                    "action_id": "holding_update_proc"
                }
            ]
        },
        back_actions("cust", m=market_name, f="holding_update")
    ]

# ────────────────────────────────────────────────────────────────────────────
# 메뉴 구조 : 거래소 선택 → (보유종목 | 매매관리) → 기능 선택 → 고객 선택 → 기능 화면
# 화면 이동 버튼은 action_id 가 "go_" 로 시작하고, value(JSON)의 "to" 에 이동할 화면을 담는다.
# ────────────────────────────────────────────────────────────────────────────
FEATURE_LABELS = {
    "holding_list": "보유종목 조회",
    "holding_update": "보유종목 수정",
    "buy": "매수",
    "sell": "매도",
    "order_open": "대기주문내역",
    "order_update": "주문정정",
    "order_cancel": "주문취소",
    "order_close": "종료주문내역",
}
HOLDING_FEATURES = ["holding_list", "holding_update"]
TRADE_FEATURES = ["buy", "sell", "order_open", "order_update", "order_cancel", "order_close"]
UPBIT_ONLY_FEATURES = ["order_update"]
MAX_LIST_ITEMS = 40
ORDER_CLOSE_PAGE_SIZE = 15

def section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}

def actions(elements: list) -> dict:
    return {"type": "actions", "elements": elements}

def button(text: str, action_id: str, value: dict, style: Optional[str] = None, confirm: Optional[str] = None) -> dict:
    element = {
        "type": "button",
        "text": {"type": "plain_text", "text": text, "emoji": True},
        "action_id": action_id,
        "value": json.dumps(value, ensure_ascii=False),
    }
    if style:
        element["style"] = style
    if confirm:
        element["confirm"] = {
            "title": {"type": "plain_text", "text": f"{text} 확인"},
            "text": {"type": "mrkdwn", "text": confirm},
            "confirm": {"type": "plain_text", "text": "실행"},
            "deny": {"type": "plain_text", "text": "취소"},
        }
    return element

def back_actions(to: str, **ctx) -> dict:
    return actions([button("⬅ 이전", "go_back", {"to": to, **ctx})])

def home_actions() -> dict:
    return actions([button("처음으로", "go_home", {"to": "market"})])

def text_input(block_id: str, action_id: str, label: str, placeholder: str, initial: Optional[str] = None) -> dict:
    element = {
        "type": "plain_text_input",
        "action_id": action_id,
        "placeholder": {"type": "plain_text", "text": placeholder},
    }
    if initial not in (None, ""):
        element["initial_value"] = str(initial)
    return {"type": "input", "block_id": block_id, "element": element, "label": {"type": "plain_text", "text": label}}

def text_sections(text: str, chunk_size: int = 2900) -> list:
    # section 블록 text 는 최대 3000자이므로 줄 단위로 나누어 여러 블록으로 표시
    blocks, chunk = [], ""
    for line in (text or "").split("\n"):
        if chunk and len(chunk) + len(line) + 1 > chunk_size:
            blocks.append(section(chunk))
            chunk = ""
        chunk = f"{chunk}\n{line}" if chunk else line
    if chunk.strip():
        blocks.append(section(chunk))
    return blocks

def fmt_num(value) -> str:
    # 정수는 천단위 콤마, 소수는 소수점 8자리까지 표시 (불필요한 0 제거)
    number = float(value)
    if number == int(number):
        return f"{int(number):,}"
    return f"{number:,.8f}".rstrip("0").rstrip(".")

def get_state_value(payload: dict, action_id: str, key: str = "value") -> Optional[str]:
    for block in payload.get("state", {}).get("values", {}).values():
        if action_id in block:
            return block[action_id].get(key)
    return None

def date_input(block_id: str, action_id: str, label: str, initial_date: str) -> dict:
    return {
        "type": "input",
        "block_id": block_id,
        "element": {
            "type": "datepicker",
            "action_id": action_id,
            "initial_date": initial_date,
            "placeholder": {"type": "plain_text", "text": f"{label}을 선택해주세요"},
        },
        "label": {"type": "plain_text", "text": label},
    }

def parse_number(value: Optional[str], label: str, allow_zero: bool = True) -> float:
    if value is None or not value.strip():
        raise ValueError(f"{label}을(를) 입력해주세요.")
    value = value.strip().replace(",", "")
    if not re.fullmatch(r"\d+(\.\d{1,8})?", value):
        raise ValueError(f"{label}은(는) 0 이상의 숫자이며 소숫점 8자리까지만 입력 가능합니다.")
    number = float(value)
    if not allow_zero and number <= 0:
        raise ValueError(f"{label}은(는) 0보다 커야 합니다.")
    return number

def normalize_result_lines(result) -> list:
    # trade_proc 처리 결과(튜플/딕셔너리 리스트)를 (표시문구, 주문번호) 리스트로 변환
    lines = []
    for line in result or []:
        if isinstance(line, dict):
            line_text, order_no = line.get("text", ""), line.get("order_no", "")
        elif isinstance(line, tuple) and len(line) == 2:
            line_text, order_no = line
        else:
            continue
        if line_text and line_text.strip():
            lines.append((line_text.strip(), order_no or ""))
    return lines

def build_result_blocks(title: str, result) -> list:
    # 처리 결과 화면 : 결과 표시 후 "처음으로" 버튼
    blocks = [section(f"*{title}*")]
    if isinstance(result, str) or result is None:
        blocks += text_sections(result or "처리 결과가 없습니다.")
    else:
        lines = normalize_result_lines(result)
        if not lines:
            blocks.append(section("처리 결과가 없습니다."))
        for line_text, order_no in lines[:MAX_LIST_ITEMS]:
            block = section(line_text)
            if order_no:
                block["accessory"] = {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "주문번호 표시"},
                    "value": order_no,
                    "action_id": "copy_uuid_action",
                }
            blocks.append(block)
        if len(lines) > MAX_LIST_ITEMS:
            blocks.append(section(f"외 {len(lines) - MAX_LIST_ITEMS}건"))
    blocks.append(home_actions())
    return blocks

def build_error_blocks(title: str, error) -> list:
    return [section(f"*{title} 중 오류 발생* : {error}"), home_actions()]

def build_market_blocks(user_id: str) -> list:
    return [
        section(f"안녕하세요 <@{user_id}>님! 어느 거래소를 선택하시겠습니까?"),
        actions([
            button("UPBIT", "go_market_upbit", {"to": "main", "m": "UPBIT"}),
            button("BITHUMB", "go_market_bithumb", {"to": "main", "m": "BITHUMB"}),
        ]),
    ]

def build_main_menu_blocks(m: str) -> list:
    return [
        section(f"*[{m}]* 메뉴를 선택하세요"),
        actions([
            button("보유종목", "go_holding_menu", {"to": "holding_menu", "m": m}),
            button("매매관리", "go_trade_menu", {"to": "trade_menu", "m": m}),
        ]),
        back_actions("market"),
    ]

def build_feature_menu_blocks(m: str, title: str, features: list) -> list:
    features = [f for f in features if m == "UPBIT" or f not in UPBIT_ONLY_FEATURES]
    return [
        section(f"*[{m}] {title}* 처리를 선택하세요"),
        actions([button(FEATURE_LABELS[f], f"go_feature_{f}", {"to": "cust", "m": m, "f": f}) for f in features]),
        back_actions("main", m=m),
    ]

def build_cust_blocks(m: str, f: str) -> list:
    cust_list = get_cust_list(m)
    blocks = [section(f"*[{m}] {FEATURE_LABELS[f]}* 고객을 선택하세요")]
    if cust_list:
        blocks.append(actions([button(c, f"go_cust_{i}", {"to": f, "m": m, "c": c}) for i, c in enumerate(cust_list)]))
    else:
        blocks.append(section("등록된 고객이 없습니다."))
    blocks.append(back_actions("holding_menu" if f in HOLDING_FEATURES else "trade_menu", m=m))
    return blocks

def build_holding_update_entry_blocks(m: str, c: str) -> list:
    holding_prd_list = get_holding_prd_list(cust_nm=c, market_name=m)
    if not holding_prd_list:
        return [section(f"*[{m}] [{c}]* 보유중인 종목이 없습니다."), back_actions("cust", m=m, f="holding_update")]
    return build_holding_update_blocks(m, c, holding_prd_list)

def build_buy_form_blocks(m: str, c: str, prefill: Optional[dict] = None) -> list:
    p = prefill or {}
    return [
        section(f"*[{m}] [{c}] 매수*\n매수가 0 입력시 현재가, 이탈가 0 입력시 금일 저가를 적용합니다."),
        text_input("buy_prd_nm_block", "buy_prd_nm", "상품명", "상품명을 입력해주세요 (예: BTC)", p.get("prd_nm")),
        text_input("buy_price_block", "buy_price", "매수가 (현재가: 0)", "매수가를 입력해주세요", p.get("buy_price")),
        text_input("buy_loss_price_block", "buy_loss_price", "이탈가 (저가: 0)", "이탈가를 입력해주세요", p.get("loss_price")),
        text_input("buy_amt_block", "buy_amt", "매수금액", "매수금액을 입력해주세요", p.get("buy_amt")),
        text_input("buy_loss_amt_block", "buy_loss_amt", "손절금액", "손절금액을 입력해주세요", p.get("loss_amt")),
        actions([button("계산", "buy_calc", {"m": m, "c": c}, style="primary")]),
        back_actions("cust", m=m, f="buy"),
    ]

def build_buy_preview_blocks(m: str, c: str, inputs: dict, buy_price: float, loss_price: float, plan: dict,
                             is_current_price: bool = False, is_low_price: bool = False) -> list:
    prd_nm = inputs["prd_nm"]
    buy_price_label = f"{fmt_num(buy_price)}원" + (" (현재가)" if is_current_price else "")
    loss_price_label = f"{fmt_num(loss_price)}원" + (" (저가)" if is_low_price else "")

    def plan_text(title: str, item: dict) -> str:
        return f"*{title}*\n매수금액: {fmt_num(item['buy_amt'])}원 | 매수량: {fmt_num(item['qty'])} | 손실금액: {fmt_num(item['loss_amt'])}원"

    order_buttons = []
    for label, action_id, key in [("손절금액", "buy_exec_loss", "loss_based"), ("매수금액", "buy_exec_amt", "amt_based")]:
        item = plan[key]
        if float(item["qty"]) > 0:
            order_buttons.append(button(
                label, action_id,
                {"m": m, "c": c, "prd_nm": prd_nm, "price": buy_price, "qty": item["qty"]},
                style="primary",
                confirm=f"[{m}] [{c}] *{prd_nm}* {fmt_num(buy_price)}원, {fmt_num(item['qty'])} 매수주문을 실행합니다.",
            ))
    order_buttons.append(button("다시계산", "go_recalc", {"to": "buy", "m": m, "c": c, "p": inputs}))

    return [
        section(f"*[{m}] [{c}] 매수주문 미리보기*\n*{prd_nm}* | 매수가: {buy_price_label} | 이탈가: {loss_price_label} | 손절율: {plan['loss_rate']}%"),
        {"type": "divider"},
        section(plan_text("손절금액 기준", plan["loss_based"])),
        {"type": "divider"},
        section(plan_text("매수금액 기준", plan["amt_based"])),
        actions(order_buttons),
        back_actions("cust", m=m, f="buy"),
    ]

def build_sell_list_blocks(m: str, c: str) -> list:
    holdings = get_sell_holding_list(cust_nm=c, market_name=m)
    blocks = [section(f"*[{m}] [{c}] 매도* 매도할 보유종목을 선택하세요")]
    if not holdings:
        blocks.append(section("보유중인 종목이 없습니다."))
    for i, h in enumerate(holdings[:MAX_LIST_ITEMS]):
        block = section(f"*{h['prd_nm']}*\n보유수량: {fmt_num(h['volume'])} | 손수익율: {h['loss_profit_rate']}% | 현재금액: {fmt_num(h['current_amt'])}원")
        block["accessory"] = button("선택", f"go_sell_item_{i}", {"to": "sell_form", "m": m, "c": c, "h": h})
        blocks.append(block)
    blocks.append(back_actions("cust", m=m, f="sell"))
    return blocks

def build_sell_form_blocks(m: str, c: str, h: dict) -> list:
    return [
        section(f"*[{m}] [{c}] 매도 - {h['prd_nm']}*\n보유수량: {fmt_num(h['volume'])} | 손수익율: {h['loss_profit_rate']}% | 현재금액: {fmt_num(h['current_amt'])}원"),
        text_input("sell_price_block", "sell_price", "매도가 (현재가: 0)", "매도가를 입력해주세요"),
        text_input("sell_rate_block", "sell_rate", "매도비율 (1~100%)", "매도비율을 입력해주세요"),
        actions([button(
            "매도", "sell_exec", {"m": m, "c": c, "h": h}, style="danger",
            confirm=f"[{m}] [{c}] *{h['prd_nm']}* 매도주문을 실행합니다.",
        )]),
        back_actions("sell", m=m, c=c),
    ]

def build_order_open_blocks(m: str, c: str) -> list:
    return build_result_blocks(f"[{m}] [{c}] 대기주문내역", get_order_open(cust_nm=c, market_name=m))

def build_order_list_blocks(m: str, c: str, f: str) -> list:
    result = get_order_open(cust_nm=c, market_name=m)
    if isinstance(result, str):
        return build_error_blocks(f"[{m}] [{c}] 대기주문 조회", result)

    blocks = [section(f"*[{m}] [{c}] {FEATURE_LABELS[f]}* 대상 주문을 선택하세요")]
    orders = [(t, o) for t, o in normalize_result_lines(result) if o]
    if not orders:
        blocks.append(section("대기중인 주문이 없습니다."))
    for i, (order_text, order_no) in enumerate(orders[:MAX_LIST_ITEMS]):
        block = section(order_text)
        block["accessory"] = button("선택", f"go_order_item_{i}", {"to": "order_form", "m": m, "c": c, "f": f, "o": order_no, "s": order_text})
        blocks.append(block)
    blocks.append(back_actions("cust", m=m, f=f))
    return blocks

def build_order_form_blocks(m: str, c: str, f: str, order_no: str, order_text: str) -> list:
    blocks = [section(f"*[{m}] [{c}] {FEATURE_LABELS[f]}*\n{order_text}\n> 주문번호: {order_no}")]
    if f == "order_update":
        blocks.append(text_input("order_price_block", "order_price", "주문가 (시장가: 0)", "정정할 주문가를 입력해주세요"))
        blocks.append(actions([button(
            "주문정정", "order_update_exec", {"m": m, "c": c, "o": order_no, "s": order_text}, style="primary",
            confirm=f"[{m}] [{c}] 주문({order_no})을 정정합니다.",
        )]))
    else:
        blocks.append(actions([button(
            "주문취소", "order_cancel_exec", {"m": m, "c": c, "o": order_no}, style="danger",
            confirm=f"[{m}] [{c}] 주문({order_no})을 취소합니다.",
        )]))
    blocks.append(back_actions(f, m=m, c=c))
    return blocks

def build_order_close_form_blocks(m: str, c: str, start: Optional[str] = None, end: Optional[str] = None) -> list:
    # 기간 선택 : 시작일(기본 올해 1월 1일) ~ 종료일(기본 현재일), 날짜 형식 YYYY-MM-DD
    today = datetime.today()
    start = start or today.replace(month=1, day=1).strftime("%Y-%m-%d")
    end = end or today.strftime("%Y-%m-%d")
    return [
        section(f"*[{m}] [{c}] 종료주문내역* 조회 기간을 선택하세요"),
        date_input("order_close_start_block", "order_close_start", "시작일", start),
        date_input("order_close_end_block", "order_close_end", "종료일", end),
        actions([button("조회", "order_close_query", {"m": m, "c": c}, style="primary")]),
        back_actions("cust", m=m, f="order_close"),
    ]

def build_order_close_result_blocks(m: str, c: str, start: str, end: str, page: int = 1) -> list:
    result = get_order_close(
        cust_nm=c,
        market_name=m,
        start_dt=start.replace("-", ""),
        end_dt=end.replace("-", "")
    )
    lines = normalize_result_lines(result)
    orders = [line for line in lines if line[1]]
    total_pages = max(1, -(-len(orders) // ORDER_CLOSE_PAGE_SIZE))
    page = min(max(1, page), total_pages)

    blocks = [section(f"*[{m}] [{c}] 종료주문내역*\n기간: {start} ~ {end} | 총 {len(orders)}건 (Page {page}/{total_pages})")]
    if not orders:
        blocks.append(section("종료된 주문이 없습니다."))
    for order_text, order_no in orders[(page - 1) * ORDER_CLOSE_PAGE_SIZE: page * ORDER_CLOSE_PAGE_SIZE]:
        block = section(order_text)
        block["accessory"] = {
            "type": "button",
            "text": {"type": "plain_text", "text": "주문번호 표시"},
            "value": order_no,
            "action_id": "copy_uuid_action",
        }
        blocks.append(block)

    nav = {"to": "order_close_result", "m": m, "c": c, "s": start, "e": end}
    page_buttons = []
    if page > 1:
        page_buttons.append(button("⬅ 이전 페이지", "go_page_prev", {**nav, "page": page - 1}))
    if page < total_pages:
        page_buttons.append(button("다음 페이지 ➡", "go_page_next", {**nav, "page": page + 1}))
    blocks.append(actions(page_buttons + [button("처음으로", "go_home", {"to": "market"})]))
    return blocks

def render_screen(nav: dict, user_id: str) -> list:
    to, m, c = nav.get("to"), nav.get("m"), nav.get("c")

    if to == "market":
        return build_market_blocks(user_id)
    if to == "main":
        return build_main_menu_blocks(m)
    if to == "holding_menu":
        return build_feature_menu_blocks(m, "보유종목", HOLDING_FEATURES)
    if to == "trade_menu":
        return build_feature_menu_blocks(m, "매매관리", TRADE_FEATURES)
    if to == "cust":
        return build_cust_blocks(m, nav["f"])
    if to == "holding_list":
        return build_result_blocks(f"[{m}] [{c}] 보유종목", get_balance(cust_nm=c, market_name=m))
    if to == "holding_update":
        return build_holding_update_entry_blocks(m, c)
    if to == "buy":
        return build_buy_form_blocks(m, c, nav.get("p"))
    if to == "sell":
        return build_sell_list_blocks(m, c)
    if to == "sell_form":
        return build_sell_form_blocks(m, c, nav["h"])
    if to == "order_open":
        return build_order_open_blocks(m, c)
    if to in ("order_update", "order_cancel"):
        return build_order_list_blocks(m, c, to)
    if to == "order_form":
        return build_order_form_blocks(m, c, nav["f"], nav["o"], nav["s"])
    if to == "order_close":
        return build_order_close_form_blocks(m, c)
    if to == "order_close_result":
        return build_order_close_result_blocks(m, c, nav["s"], nav["e"], nav.get("page", 1))
    raise ValueError(f"알 수 없는 화면입니다: {to}")

def get_tunnel_url(nickname: Optional[str] = None) -> str:
    """
    cloudflared 임시 접속 URL을 조회하고, 닉네임이 주어지면 쿼리 파라미터로 덧붙인다.
    """
    url_file = "/home/terra/log/tunnel/universe_tunnel_url.txt"
    try:
        with open(url_file, "r") as f:
            result = f.read().strip()
        if not result:
            return "현재 cloudflared 임시 URL을 찾을 수 없습니다."
    except Exception as e:
        return f"URL 조회 실패: {e}"

    if nickname:
        separator = "&" if "?" in result else "?"
        result = f"{result}{separator}nickname={quote(nickname)}"

    return result

# Slash Command 처리
@app.post("/slack/command")
async def slack_command(request: Request):
    # Slack 재시도(retry) 요청은 중복 처리 방지를 위해 무시
    if request.headers.get("X-Slack-Retry-Num"):
        return JSONResponse(content="")

    form = await request.form()
    command = form.get("command")
    text = form.get("text")
    user_id = form.get("user_id")

    if command == "/info":
        nickname = text.strip() if text else None
        result = get_tunnel_url(nickname)

        return JSONResponse({
            "response_type": "in_channel",
            "text": f"🌐 Universe Balance Info 접속 URL: {result}"
        })
    
    else:
        # Slack으로 인터랙티브 버튼 리턴
        return JSONResponse(
            content={
                "response_type": "ephemeral",
                "blocks": build_market_blocks(user_id)
            }
        )

# 버튼 클릭 이벤트 처리
@app.post("/slack/interactivity")
async def slack_interactivity(request: Request):
    # Slack 재시도(retry) 요청은 중복 처리(중복 주문 등) 방지를 위해 무시
    if request.headers.get("X-Slack-Retry-Num"):
        return JSONResponse(content="")

    form = await request.form()
    payload = json.loads(form.get("payload"))

    # Slack에는 즉시 ACK 응답하고, 실제 처리는 백그라운드에서 진행 (3초 제한으로 인한 타임아웃/재시도 방지)
    asyncio.create_task(process_slack_interactivity(payload))

    return JSONResponse(content="")

async def process_slack_interactivity(payload: dict):
    action_id = payload["actions"][0]["action_id"]
    action_type = payload["actions"][0].get("type")
    response_url = payload["response_url"]

    # 콤보박스(select) 선택 자체는 값 저장용으로만 사용되며, 제출 버튼 클릭시에만 처리
    # (input_prd_nm 은 보유종목 수정 화면에서 선택시 기존 값을 표시하기 위해 예외적으로 처리)
    if action_type in ("static_select", "external_select", "users_select", "conversations_select", "channels_select", "multi_static_select", "datepicker") and action_id != "input_prd_nm":
        return

    message = {}

    user_id = payload.get("user", {}).get("id", "")

    if action_id.startswith("go_"):
        nav = json.loads(payload["actions"][0]["value"])
        try:
            blocks = render_screen(nav, user_id)
        except Exception as e:
            blocks = build_error_blocks("화면 조회", e)

        message = {
            "response_type": "ephemeral",
            "replace_original": True,
            "text": "메뉴",
            "blocks": blocks
        }

    elif action_id == "buy_calc":
        selection = json.loads(payload["actions"][0]["value"])
        market_name, cust_nm = selection["m"], selection["c"]
        inputs = {
            "prd_nm": (get_state_value(payload, "buy_prd_nm") or "").strip().upper(),
            "buy_price": get_state_value(payload, "buy_price"),
            "loss_price": get_state_value(payload, "buy_loss_price"),
            "buy_amt": get_state_value(payload, "buy_amt"),
            "loss_amt": get_state_value(payload, "buy_loss_amt"),
        }

        try:
            if not re.fullmatch(r"[A-Z0-9]+", inputs["prd_nm"]):
                raise ValueError("상품명은 영문/숫자만 입력 가능합니다.")
            buy_price_in = parse_number(inputs["buy_price"], "매수가")
            loss_price_in = parse_number(inputs["loss_price"], "이탈가")
            buy_amt = parse_number(inputs["buy_amt"], "매수금액", allow_zero=False)
            loss_amt = parse_number(inputs["loss_amt"], "손절금액", allow_zero=False)

            # 매수가 0 : 현재가, 이탈가 0 : 금일 저가
            ticker = get_ticker(market_name, inputs["prd_nm"])
            buy_price = ticker["trade_price"] if buy_price_in == 0 else buy_price_in
            loss_price = ticker["low_price"] if loss_price_in == 0 else loss_price_in
            if buy_price <= loss_price:
                raise ValueError(f"매수가({fmt_num(buy_price)})가 이탈가({fmt_num(loss_price)}) 이하입니다.")

            plan = calc_buy_plan(buy_price, loss_price, buy_amt, loss_amt)
            blocks = build_buy_preview_blocks(
                market_name, cust_nm, inputs, buy_price, loss_price, plan,
                is_current_price=buy_price_in == 0,
                is_low_price=loss_price_in == 0
            )
        except Exception as e:
            blocks = [section(f"*[{market_name}] [{cust_nm}] 매수 계산 중 오류 발생* : {e}")] + build_buy_form_blocks(market_name, cust_nm, inputs)

        message = {
            "response_type": "ephemeral",
            "replace_original": True,
            "text": "매수주문 미리보기",
            "blocks": blocks
        }

    elif action_id in ("buy_exec_loss", "buy_exec_amt"):
        selection = json.loads(payload["actions"][0]["value"])
        market_name, cust_nm = selection["m"], selection["c"]
        title = f"[{market_name}] [{cust_nm}] 매수주문"

        try:
            # 미리보기에서 산정한 매수가/매수량으로 지정가 매수
            order_info = buy_proc(
                cust_nm=cust_nm,
                market_name=market_name,
                gubun="custom",
                prd_nm=selection["prd_nm"],
                price=selection["price"],
                custom_volumn=selection["qty"]
            )
            blocks = build_result_blocks(title, order_info)
        except Exception as e:
            blocks = build_error_blocks(title, e)

        message = {
            "response_type": "ephemeral",
            "replace_original": True,
            "text": title,
            "blocks": blocks
        }

    elif action_id == "sell_exec":
        selection = json.loads(payload["actions"][0]["value"])
        market_name, cust_nm, holding = selection["m"], selection["c"], selection["h"]
        prd_nm = holding["prd_nm"]
        title = f"[{market_name}] [{cust_nm}] 매도주문"

        try:
            price_in = parse_number(get_state_value(payload, "sell_price"), "매도가")
            sell_rate = parse_number(get_state_value(payload, "sell_rate"), "매도비율", allow_zero=False)
            if not 1 <= sell_rate <= 100:
                raise ValueError("매도비율은 1~100 사이로 입력해주세요.")

            # 매도가 0 : 현재가
            price = get_ticker(market_name, prd_nm)["trade_price"] if price_in == 0 else price_in
        except Exception as e:
            blocks = [section(f"*{title} 입력 오류* : {e}")] + build_sell_form_blocks(market_name, cust_nm, holding)
        else:
            try:
                order_info = sell_proc(
                    cust_nm=cust_nm,
                    market_name=market_name,
                    gubun="rate",
                    prd_nm=prd_nm,
                    price=price,
                    custom_volumn_rate=sell_rate
                )
                blocks = build_result_blocks(title, order_info)
            except Exception as e:
                blocks = build_error_blocks(title, e)

        message = {
            "response_type": "ephemeral",
            "replace_original": True,
            "text": title,
            "blocks": blocks
        }

    elif action_id == "order_update_exec":
        selection = json.loads(payload["actions"][0]["value"])
        market_name, cust_nm = selection["m"], selection["c"]
        title = f"[{market_name}] [{cust_nm}] 주문정정"

        try:
            price = parse_number(get_state_value(payload, "order_price"), "주문가")
        except Exception as e:
            blocks = [section(f"*{title} 입력 오류* : {e}")] + build_order_form_blocks(market_name, cust_nm, "order_update", selection["o"], selection["s"])
        else:
            try:
                result = order_update(cust_nm=cust_nm, market_name=market_name, order_no=selection["o"], price=price)
                blocks = build_result_blocks(title, result)
            except Exception as e:
                blocks = build_error_blocks(title, e)

        message = {
            "response_type": "ephemeral",
            "replace_original": True,
            "text": title,
            "blocks": blocks
        }

    elif action_id == "order_cancel_exec":
        selection = json.loads(payload["actions"][0]["value"])
        market_name, cust_nm = selection["m"], selection["c"]
        title = f"[{market_name}] [{cust_nm}] 주문취소"

        try:
            result = order_cancel(cust_nm=cust_nm, market_name=market_name, order_no=selection["o"])
            blocks = build_result_blocks(title, result)
        except Exception as e:
            blocks = build_error_blocks(title, e)

        message = {
            "response_type": "ephemeral",
            "replace_original": True,
            "text": title,
            "blocks": blocks
        }

    elif action_id == "input_prd_nm":
        try:
            block_id = payload["actions"][0]["block_id"]
            _, encoded_ctx = block_id.split("|", 1)
            ctx = decode_value(encoded_ctx)
            market_name = ctx["market_name"]
            cust_nm = ctx["cust_nm"]
            selected_prd_nm = payload["actions"][0]["selected_option"]["value"]

            # 선택한 보유종목의 기존 이탈가/수행가/최종이탈가 조회
            holding_prd_list = get_holding_prd_list(cust_nm=cust_nm, market_name=market_name)
            prices = get_holding_prices(cust_nm=cust_nm, market_name=market_name, prd_nm=selected_prd_nm)

            # 기존 매매계획 선택값 유지
            trading_plan_option = None
            for block in payload.get("state", {}).get("values", {}).values():
                if "input_trading_plan" in block and block["input_trading_plan"].get("selected_option"):
                    trading_plan_option = block["input_trading_plan"]["selected_option"]

            message = {
                "response_type": "ephemeral",
                "replace_original": True,
                "blocks": build_holding_update_blocks(
                    market_name, cust_nm, holding_prd_list,
                    selected_prd_nm=selected_prd_nm,
                    prices=prices,
                    trading_plan_option=trading_plan_option
                )
            }
        except Exception as e:
            message = {
                "response_type": "ephemeral",
                "replace_original": True,
                "text": f"보유종목 정보 조회 중 오류 발생 : {e}"
            }

    elif action_id == "holding_update_proc":
        selection = json.loads(payload["actions"][0]["value"])
        market_name = selection["market_name"]
        cust_nm = selection["cust_nm"]
        state_values = payload["state"]["values"]
        prd_nm = None
        stop_price = None
        action_price = None
        exit_price = None
        trading_plan = None

        try:
            for block_id, block in state_values.items():
                if "input_prd_nm" in block:
                    selected_prd_nm = block["input_prd_nm"].get("selected_option")

                    # 유효성 검사
                    if not selected_prd_nm:
                        raise ValueError("상품명을 선택해주세요.")
                    prd_nm = selected_prd_nm["value"]

                if "input_stop_price" in block:
                    stop_price = parse_optional_price(block["input_stop_price"]["value"], "이탈가")

                if "input_action_price" in block:
                    action_price = parse_optional_price(block["input_action_price"]["value"], "수행가")

                if "input_exit_price" in block:
                    exit_price = parse_optional_price(block["input_exit_price"]["value"], "최종이탈가")

                if "input_trading_plan" in block:
                    selected_option = block["input_trading_plan"].get("selected_option")

                    # 유효성 검사
                    if not selected_option:
                        raise ValueError("매매계획을 선택해주세요.")
                    trading_plan = None if selected_option["value"] == "NULL" else selected_option["value"]

            # 보유종목 정보 변경
            result = holding_update(
                cust_nm=cust_nm,
                market_name=market_name,
                prd_nm=prd_nm,
                stop_price=stop_price,
                action_price=action_price,
                exit_price=exit_price,
                trading_plan=trading_plan
            )

            message = {
                "response_type": "ephemeral",
                "replace_original": True,
                "text": f"[{market_name}] [{cust_nm}] 보유종목 수정",
                "blocks": build_result_blocks(f"[{market_name}] [{cust_nm}] 보유종목 수정", result)
            }
        except Exception as e:
            message = {
                "response_type": "ephemeral",
                "replace_original": True,
                "text": f"[{market_name}] [{cust_nm}] 보유종목 수정",
                "blocks": build_error_blocks(f"[{market_name}] [{cust_nm}] 보유종목 수정", e)
            }

    elif action_id == "interest_action":
        selection = json.loads(payload["actions"][0]["value"])
        market_name = selection["market_name"]
        cust_nm = selection["cust_nm"]

        interest_buttons = []
        for text, action_id in [("관심종목 조회", "interest_list_action"), ("관심종목 등록/수정", "interest_update_action")]:
            value = json.dumps({"market_name": market_name, "cust_nm": cust_nm})
            interest_buttons.append({
                "type": "button",
                "text": { "type": "plain_text", "text": text },
                "value": value,
                "action_id": action_id
            })

        message = {
            "response_type": "ephemeral",
            "replace_original": True,
            "text": f"*[{market_name}] {cust_nm}*의 관심종목 관리를 선택하세요.",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "관심종목 처리를 선택하세요"
                    }
                },
                {
                    "type": "actions",
                    "elements": interest_buttons
                }
            ]
        }

    elif action_id == "interest_list_action":
        selection = json.loads(payload["actions"][0]["value"])
        market_name = selection["market_name"]
        cust_nm = selection["cust_nm"]

        try:
            # 관심종목 조회
            interest_list = get_interest_list(market_name=market_name)

            message = {
                "response_type": "ephemeral",
                "replace_original": True,
                "text": f"*[{market_name}] 관심종목 조회*\n{interest_list}"
            }
        except Exception as e:
            message = {
                "response_type": "ephemeral",
                "replace_original": True,
                "text": f"*[{market_name}] 관심종목 조회 중 오류 발생* : {e}"
            }

    elif action_id == "interest_update_action":
        selection = json.loads(payload["actions"][0]["value"])
        market_name = selection["market_name"]
        cust_nm = selection["cust_nm"]
        value = json.dumps({"market_name": market_name, "cust_nm": cust_nm})

        message = {
            "response_type": "ephemeral",
            "replace_original": True,
            "blocks": [
                {
                    "type": "input",
                    "block_id": "prd_nm_input_block",
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "input_prd_nm",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "상품명을 입력해주세요"
                        }
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "상품명"
                    }
                },
                {
                    "type": "input",
                    "block_id": "through_price_input_block",
                    "optional": True,
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "input_through_price",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "돌파가를 입력해주세요 (선택)"
                        }
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "돌파가"
                    }
                },
                {
                    "type": "input",
                    "block_id": "leave_price_input_block",
                    "optional": True,
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "input_leave_price",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "이탈가를 입력해주세요 (선택)"
                        }
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "이탈가"
                    }
                },
                {
                    "type": "input",
                    "block_id": "resist_price_input_block",
                    "optional": True,
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "input_resist_price",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "저항가를 입력해주세요 (선택)"
                        }
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "저항가"
                    }
                },
                {
                    "type": "input",
                    "block_id": "support_price_input_block",
                    "optional": True,
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "input_support_price",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "지지가를 입력해주세요 (선택)"
                        }
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "지지가"
                    }
                },
                {
                    "type": "input",
                    "block_id": "trend_high_price_input_block",
                    "optional": True,
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "input_trend_high_price",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "추세고가를 입력해주세요 (선택)"
                        }
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "추세고가"
                    }
                },
                {
                    "type": "input",
                    "block_id": "trend_low_price_input_block",
                    "optional": True,
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "input_trend_low_price",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "추세저가를 입력해주세요 (선택)"
                        }
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "추세저가"
                    }
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "관심종목 등록/수정",
                                "emoji": True
                            },
                            "value": value,
                            "action_id": "interest_update_proc"
                        }
                    ]
                }
            ]
        }

    elif action_id == "interest_update_proc":
        selection = json.loads(payload["actions"][0]["value"])
        market_name = selection["market_name"]
        cust_nm = selection["cust_nm"]
        state_values = payload["state"]["values"]
        prd_nm = None
        through_price = None
        leave_price = None
        resist_price = None
        support_price = None
        trend_high_price = None
        trend_low_price = None

        try:
            for block_id, block in state_values.items():
                if "input_prd_nm" in block:
                    prd_nm = block["input_prd_nm"]["value"]

                    # 유효성 검사
                    if not prd_nm:
                        raise ValueError("상품명을 입력해주세요.")
                    # 영문 대문자만 허용 (소문자는 upper 처리)
                    if not re.fullmatch(r'[A-Za-z]+', prd_nm):
                        raise ValueError("상품명은 영문 알파벳만 입력 가능합니다.")
                    prd_nm = prd_nm.upper()

                if "input_through_price" in block:
                    through_price = parse_optional_price(block["input_through_price"]["value"], "돌파가")

                if "input_leave_price" in block:
                    leave_price = parse_optional_price(block["input_leave_price"]["value"], "이탈가")

                if "input_resist_price" in block:
                    resist_price = parse_optional_price(block["input_resist_price"]["value"], "저항가")

                if "input_support_price" in block:
                    support_price = parse_optional_price(block["input_support_price"]["value"], "지지가")

                if "input_trend_high_price" in block:
                    trend_high_price = parse_optional_price(block["input_trend_high_price"]["value"], "추세고가")

                if "input_trend_low_price" in block:
                    trend_low_price = parse_optional_price(block["input_trend_low_price"]["value"], "추세저가")

            # 관심종목 등록
            result = interest_update(
                market_name=market_name,
                prd_nm="KRW-" + prd_nm,
                through_price=through_price,
                leave_price=leave_price,
                resist_price=resist_price,
                support_price=support_price,
                trend_high_price=trend_high_price,
                trend_low_price=trend_low_price
            )

            message = {
                "response_type": "ephemeral",
                "replace_original": True,
                "text": f"*[{market_name}] 관심종목 등록/수정*\n{result}"
            }
        except Exception as e:
            message = {
                "response_type": "ephemeral",
                "replace_original": True,
                "text": f"*[{market_name}] 관심종목 등록/수정 중 오류 발생* : {e}"
            }

    elif action_id == "order_close_query":
        selection = json.loads(payload["actions"][0]["value"])
        market_name, cust_nm = selection["m"], selection["c"]
        start = get_state_value(payload, "order_close_start", "selected_date")
        end = get_state_value(payload, "order_close_end", "selected_date")

        try:
            if not start or not end:
                raise ValueError("시작일과 종료일을 선택해주세요.")
            if start > end:
                raise ValueError(f"시작일({start})이 종료일({end})보다 늦습니다.")
            blocks = build_order_close_result_blocks(market_name, cust_nm, start, end)
        except ValueError as e:
            blocks = [section(f"*[{market_name}] [{cust_nm}] 종료주문내역 입력 오류* : {e}")] + build_order_close_form_blocks(market_name, cust_nm, start, end)
        except Exception as e:
            blocks = build_error_blocks(f"[{market_name}] [{cust_nm}] 종료주문내역 조회", e)

        message = {
            "response_type": "ephemeral",
            "replace_original": True,
            "text": f"[{market_name}] [{cust_nm}] 종료주문내역",
            "blocks": blocks
        }

    elif action_id == "copy_uuid_action":
        uuid_val = payload["actions"][0]["value"]
        message = {
            "response_type": "ephemeral",
            "replace_original": False,
            "text": f"{uuid_val}"
        }
    
    else:
        # 기타 예외
        message = {
            "response_type": "ephemeral",
            "text": f"알 수 없는 액션입니다: {action_id}"
        }

    # Slack 응답 전송
    # try:
    #     res = requests.post(response_url, json=message)
    #     if not res.ok:
    #         print(f"Slack 응답 실패: {res.status_code} - {res.text}")
    # except requests.exceptions.RequestException as e:
    #     print(f"Slack 전송 실패: {e}")
    try:
        response = requests.post(response_url, json=message)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print("Slack 응답 실패:", e)
        print("전송된 message:", json.dumps(message, ensure_ascii=False, indent=2))

@click.group()
def cli():
    pass

@cli.command()
def montecarlo() -> None:
    print("111")
    montecarlo_()

def run() -> None:
    cli()

if __name__ == "__main__":
    run()            