from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import json
import requests
from routers.trade_proc import get_balance, buy_proc, sell_proc, get_order_open, order_update, order_cancel, get_order_close
from typing import List, Tuple, Union, Optional
import re
import base64
from datetime import datetime, timedelta
# from routers import auth as auth_router
from routers import cust_mng as cust_mng_router
from routers import trade_mng as trade_mng_router
import click
import subprocess
from montecarlo import montecarlo as montecarlo_

app = FastAPI()

# app.include_router(auth_router.router, prefix="/api/auth", tags=["auth"])
app.include_router(cust_mng_router.router, prefix="/api/cust_mng", tags=["cust_mng"])
app.include_router(trade_mng_router.router, prefix="/api/trade_mng", tags=["trade_mng"])

MAX_BLOCKS = 50
MAX_VALUE_LENGTH = 2000
MAX_TEXT_LENGTH = 3000

@app.get("/")
def read_root():
    return {"message": "Hello, World!"}

def build_blocks(
    text_lines: list[dict],
    market_name: str,
    cust_nm: str,
    prd_nm: str = None,
    order_no: str = None,
    start_dt: str = None,
    page: int = 1,
    page_size: int = 15
) -> list[dict]:
    blocks = []

    # 헤더 블록
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"*[{market_name}] [{cust_nm}] 주문 조회 (Page {page})*"
        }
    })

    # 페이지네이션 적용
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    page_lines = text_lines[start_idx:end_idx]

    for line in page_lines:
        if isinstance(line, dict):
            order_text = line.get("text", "").strip()
            extracted_order_no = line.get("order_no")
        elif isinstance(line, tuple) and len(line) == 2:
            order_text, extracted_order_no = line
            order_text = order_text.strip()
        else:
            continue

        # 주문 정보 텍스트 블록
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": order_text
            }
        })

        # 주문 버튼 블록 (order_no 존재 시만)
        if extracted_order_no and extracted_order_no.strip():
            value_payload = {
                "market_name": market_name,
                "cust_nm": cust_nm,
                "order_no": extracted_order_no
            }
            encoded_value = encode_value(value_payload)

            blocks.append({
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "주문번호 표시"},
                        "value": extracted_order_no,
                        "action_id": "copy_uuid_action"
                    }
                ]
            })

        # 구분선
        blocks.append({"type": "divider"})

    def safe(val):
        return val if val is not None else "_"

    # 이전 페이지 버튼
    if page > 1:
        prev_payload = {
            "market_name": market_name,
            "cust_nm": cust_nm,
            "prd_nm": safe(prd_nm),
            "order_no": safe(order_no),
            "start_dt": safe(start_dt),
            "page": page - 1,
            "page_size": page_size
        }
        encoded_prev = encode_value(prev_payload)
        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "⬅ 이전"},
                    "value": encoded_prev,
                    "action_id": "paginate_order_close"
                }
            ]
        })

    # 다음 페이지 버튼
    if end_idx < len(text_lines):
        next_payload = {
            "market_name": market_name,
            "cust_nm": cust_nm,
            "prd_nm": safe(prd_nm),
            "order_no": safe(order_no),
            "start_dt": safe(start_dt),
            "page": page + 1,
            "page_size": page_size
        }
        encoded_next = encode_value(next_payload)
        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "다음 ➡"},
                    "value": encoded_next,
                    "action_id": "paginate_order_close"
                }
            ]
        })

    return blocks

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

# Slash Command 처리
@app.post("/slack/command")
async def slack_command(request: Request):
    form = await request.form()
    command = form.get("command")
    text = form.get("text")
    user_id = form.get("user_id")

    if command == "/info":
        log_file = "/home/terra/log/tunnel/universe_tunnel.log"
        try:
            # grep으로 URL만 추출, tail -1로 마지막 URL 가져오기
            result = subprocess.check_output(
                f"grep -o 'https://[a-zA-Z0-9.-]*\.trycloudflare\.com' {log_file} | tail -1",
                shell=True
            ).decode().strip()

            if not result:
                result = "현재 cloudflared 임시 URL을 찾을 수 없습니다."

        except Exception as e:
            result = f"URL 조회 실패: {e}"

        return JSONResponse({
            "response_type": "in_channel",
            "text": f"🌐 Universe Balance Info 접속 URL: {result}"
        })
    
    else:
        # Slack으로 인터랙티브 버튼 리턴
        return JSONResponse(
            content={
                "response_type": "ephemeral",
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"안녕하세요 <@{user_id}>님! 어느 거래소를 선택하시겠습니까?"
                        }
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {
                                    "type": "plain_text",
                                    "text": "UPBIT",
                                },
                                "value": "UPBIT",
                                "action_id": "select_upbit"
                            },
                            {
                                "type": "button",
                                "text": {
                                    "type": "plain_text",
                                    "text": "BITHUMB",
                                },
                                "value": "BITHUMB",
                                "action_id": "select_bithumb"
                            }
                        ]
                    }
                ]
            }
        )

# 버튼 클릭 이벤트 처리
@app.post("/slack/interactivity")
async def slack_interactivity(request: Request):
    form = await request.form()
    payload = json.loads(form.get("payload"))

    action_id = payload["actions"][0]["action_id"]
    response_url = payload["response_url"]

    message = {}

    if action_id in ["select_upbit", "select_bithumb"]:
        market_name = payload["actions"][0]["value"]
        
        customer_buttons = []
        for cust_nm in ["phills2", "mama", "honey"]:
            customer_buttons.append({
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": cust_nm,
                    "emoji": True
                },
                "value": json.dumps({"market_name": market_name, "cust_nm": cust_nm}),
                "action_id": "select_customer_"+cust_nm
            })

        message = {
            "response_type": "ephemeral",
            "replace_original": True,
            "text": "고객 선택",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "어느 고객을 선택하시겠습니까?"
                    }
                },
                {
                    "type": "actions",
                    "elements": customer_buttons
                }
            ]
        }

    elif action_id  in ["select_customer_phills2", "select_customer_mama", "select_customer_honey"]:
        selection = json.loads(payload["actions"][0]["value"])
        market_name = selection["market_name"]
        cust_nm = selection["cust_nm"]
        
        action_buttons = []
        for text, action_id in [("잔고정보", "balance_action"), ("매매관리", "mng_action"), ("매매계획", "plan_action")]:
            value = json.dumps({"market_name": market_name, "cust_nm": cust_nm})
            action_buttons.append({
                "type": "button",
                "text": { "type": "plain_text", "text": text },
                "value": value,
                "action_id": action_id
            })

        message = {
            "response_type": "ephemeral",
            "replace_original": True,
            "blocks": [
                {
                    "type": "actions",
                    "elements": action_buttons
                }
            ]
        }
    
    elif action_id == "balance_action":
        selection = json.loads(payload["actions"][0]["value"])
        market_name = selection["market_name"]
        cust_nm = selection["cust_nm"]

        try:
            # 잔고 조회
            balance_list = get_balance(cust_nm=cust_nm, market_name=market_name)
            
            message = {
                "response_type": "ephemeral",
                "replace_original": True,
                "text": f"*[{market_name}] [{cust_nm}] : 잔고정보*\n{balance_list}"
            }
        except Exception as e:
            message = {
                "response_type": "ephemeral",
                "replace_original": True,
                "text": f"*[{market_name}] [{cust_nm}] 잔고정보 조회 중 오류 발생* : {e}"
            }

    elif action_id == "mng_action":
        selection = json.loads(payload["actions"][0]["value"])
        market_name = selection["market_name"]
        cust_nm = selection["cust_nm"]
        
        mng_buttons = []
        
        if market_name == "UPBIT":
            for text, action_id in [("매수", "buy_action"), ("매도", "sell_action"), ("대기주문내역", "order_open_action"), ("주문정정", "order_update_action"), ("주문취소", "order_cancel_action"), ("종료주문내역", "order_close_action")]:
                value = json.dumps({"market_name": market_name, "cust_nm": cust_nm})
                mng_buttons.append({
                    "type": "button",
                    "text": { "type": "plain_text", "text": text },
                    "value": value,
                    "action_id": action_id
                })
        else:
            for text, action_id in [("매수", "buy_action"), ("매도", "sell_action"), ("대기주문내역", "order_open_action"), ("주문취소", "order_cancel_action"), ("종료주문내역", "order_close_action")]:
                value = json.dumps({"market_name": market_name, "cust_nm": cust_nm})
                mng_buttons.append({
                    "type": "button",
                    "text": { "type": "plain_text", "text": text },
                    "value": value,
                    "action_id": action_id
                })

        message = {
            "response_type": "ephemeral",
            "replace_original": True,
            "text": f"*[{market_name}] {cust_nm}*의 매매관리를 선택하세요.",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "매매 처리를 선택하세요"
                    }
                },
                {
                    "type": "actions",
                    "elements": mng_buttons
                }
            ]
        }

    elif action_id == "buy_action":
        selection = json.loads(payload["actions"][0]["value"])
        market_name = selection["market_name"]
        cust_nm = selection["cust_nm"]
        value = json.dumps({"market_name": market_name, "cust_nm": cust_nm})
        
        buy_buttons = []
        for text, action_id in [("손절금액 매수", "cut_buy_action"), ("매수금액 매수", "amt_buy_action"), ("현재가 매수", "direct_buy_action"), ("매수량 매수가", "custom_buy_action")]:
            value = json.dumps({"market_name": market_name, "cust_nm": cust_nm})
            buy_buttons.append({
                "type": "button",
                "text": { "type": "plain_text", "text": text },
                "value": value,
                "action_id": action_id
            })

        message = {
            "response_type": "ephemeral",
            "replace_original": True,
            "text": f"*[{market_name}] {cust_nm}*의 매수 방식을 선택하세요.",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "매수 방식을 선택하세요"
                    }
                },
                {
                    "type": "actions",
                    "elements": buy_buttons
                }
            ]
        }
    
    elif action_id == "cut_buy_action":
        selection = json.loads(payload["actions"][0]["value"])
        market_name = selection["market_name"]
        cust_nm = selection["cust_nm"]
        value = json.dumps({"market_name": market_name, "cust_nm": cust_nm, "gubun": "cut"})
        
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
                    "block_id": "price_input_block",
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "input_price",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "매수가를 입력해주세요"
                        }
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "매수가"
                    }
                },
                {
                    "type": "input",
                    "block_id": "cut_price_input_block",
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "input_cut_price",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "이탈가를 입력해주세요"
                        }
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "이탈가"
                    }
                },
                {
                    "type": "input",
                    "block_id": "cut_amt_input_block",
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "input_cut_amt",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "손절금액을 입력해주세요"
                        }
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "손절금액"
                    }
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "손절금액 매수",
                                "emoji": True
                            },
                            "value": value,
                            "action_id": "buy_proc"
                        }
                    ]
                }
            ]
        }
        
    elif action_id == "amt_buy_action":
        selection = json.loads(payload["actions"][0]["value"])
        market_name = selection["market_name"]
        cust_nm = selection["cust_nm"]
        value = json.dumps({"market_name": market_name, "cust_nm": cust_nm, "gubun": "amt"})
        
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
                    "block_id": "price_input_block",
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "input_price",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "매수가를 입력해주세요"
                        }
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "매수가"
                    }
                },
                {
                    "type": "input",
                    "block_id": "buy_amt_input_block",
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "input_buy_amt",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "매수금액을 입력해주세요"
                        }
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "매수금액"
                    }
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "매수금액 매수",
                                "emoji": True
                            },
                            "value": value,
                            "action_id": "buy_proc"
                        }
                    ]
                }
            ]
        }    
    
    elif action_id == "direct_buy_action":
        selection = json.loads(payload["actions"][0]["value"])
        market_name = selection["market_name"]
        cust_nm = selection["cust_nm"]
        value = json.dumps({"market_name": market_name, "cust_nm": cust_nm, "gubun": "direct"})
        
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
                    "block_id": "buy_amt_input_block",
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "input_buy_amt",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "매수금액을 입력해주세요"
                        }
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "매수금액"
                    }
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "현재가 매수",
                                "emoji": True
                            },
                            "value": value,
                            "action_id": "buy_proc"
                        }
                    ]
                }
            ]
        }   
        
    elif action_id == "custom_buy_action":
        selection = json.loads(payload["actions"][0]["value"])
        market_name = selection["market_name"]
        cust_nm = selection["cust_nm"]
        value = json.dumps({"market_name": market_name, "cust_nm": cust_nm, "gubun": "custom"})
        
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
                    "block_id": "price_input_block",
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "input_price",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "매수가를 입력해주세요"
                        }
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "매수가"
                    }
                },
                {
                    "type": "input",
                    "block_id": "volumn_input_block",
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "input_volumn",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "매수량를 입력해주세요"
                        }
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "매수량"
                    }
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "매수량 매수가 매수",
                                "emoji": True
                            },
                            "value": value,
                            "action_id": "buy_proc"
                        }
                    ]
                }
            ]
        }   
    
    elif action_id == "buy_proc":
        selection = json.loads(payload["actions"][0]["value"])
        market_name = selection["market_name"]
        cust_nm = selection["cust_nm"]
        gubun = selection.get("gubun")
        state_values = payload["state"]["values"]
        prd_nm = None
        price = None
        cut_price = None
        custom_volumn = None
        buy_amt = None
        cut_amt = None

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
                    
                if "input_price" in block:
                    price_input = block["input_price"]["value"]
                    
                    # 유효성 검사
                    if not price_input:
                        raise ValueError("매수가를 입력해주세요.")
                    # 숫자인지 확인 (정수 또는 소수, 음수 불가)
                    if not re.fullmatch(r"\d+(\.\d{1,5})?", price_input):
                        raise ValueError("매수가는 0 이상의 숫자이며 소숫점 5자리까지만 입력 가능합니다.")

                    # 문자열을 float으로 변환
                    price = float(price_input)

                    # 0 이상의 값인지 확인
                    if price < 0:
                        raise ValueError("매수가는 0 이상의 숫자여야 합니다.")
                    
                if "input_cut_price" in block:
                    cut_price_input = block["input_cut_price"]["value"]
                    
                    # 유효성 검사
                    if not cut_price_input:
                        raise ValueError("이탈가를 입력해주세요.")
                    # 숫자인지 확인 (정수 또는 소수, 음수 불가)
                    if not re.fullmatch(r"\d+(\.\d{1,5})?", cut_price_input):
                        raise ValueError("이탈가는 0 이상의 숫자이며 소숫점 5자리까지만 입력 가능합니다.")

                    # 문자열을 float으로 변환
                    cut_price = float(cut_price_input)

                    # 0 이상의 값인지 확인
                    if cut_price < 0:
                        raise ValueError("이탈가는 0 이상의 숫자여야 합니다.")    
                
                if "input_volumn" in block:
                    volumn_input = block["input_volumn"]["value"]
                    
                    # 유효성 검사
                    if not volumn_input:
                        raise ValueError("매수량을 입력해주세요.")
                    # 숫자인지 확인 (정수 또는 소수, 음수 불가)
                    if not re.fullmatch(r"\d+(\.\d{1,5})?", volumn_input):
                        raise ValueError("매수량은 0 이상의 숫자이며 소숫점 5자리까지만 입력 가능합니다.")

                    # 문자열을 float으로 변환
                    custom_volumn = float(volumn_input)

                    # 0 이상의 값인지 확인
                    if custom_volumn < 0:
                        raise ValueError("매수량은 0 이상의 숫자여야 합니다.")    
                    
                if "input_buy_amt" in block:
                    buy_amt_input = block["input_buy_amt"]["value"]
                    
                    # 유효성 검사
                    if not buy_amt_input:
                        raise ValueError("매수금액을 입력해주세요.")
                    # 숫자인지 확인 (정수 또는 소수, 음수 불가)
                    if not re.fullmatch(r"\d+(\.\d{1,5})?", buy_amt_input):
                        raise ValueError("매수금액은 0 이상의 숫자이며 소숫점 5자리까지만 입력 가능합니다.")

                    # 문자열을 float으로 변환
                    buy_amt = float(buy_amt_input)

                    # 0 이상의 값인지 확인
                    if buy_amt < 0:
                        raise ValueError("매수금액은 0 이상의 숫자여야 합니다.")    
                    
                if "input_cut_amt" in block:
                    cut_amt_input = block["input_cut_amt"]["value"]
                    
                    # 유효성 검사
                    if not cut_amt_input:
                        raise ValueError("손절금액을 입력해주세요.")
                    # 숫자인지 확인 (정수 또는 소수, 음수 불가)
                    if not re.fullmatch(r"\d+(\.\d{1,5})?", cut_amt_input):
                        raise ValueError("손절금액은 0 이상의 숫자이며 소숫점 5자리까지만 입력 가능합니다.")

                    # 문자열을 float으로 변환
                    cut_amt = float(cut_amt_input)

                    # 0 이상의 값인지 확인
                    if cut_amt < 0:
                        raise ValueError("손절금액은 0 이상의 숫자여야 합니다.")       

            # 매수 처리
            order_info = buy_proc(cust_nm=cust_nm, market_name=market_name, gubun=gubun, prd_nm=prd_nm, price=price, cut_price=cut_price, custom_volumn=custom_volumn, buy_amt=buy_amt, cut_amt=cut_amt)
            blocks = build_blocks(order_info, market_name, cust_nm)
            
            message = {
                "response_type": "ephemeral",
                "replace_original": True,
                "text": f"*[{market_name}] [{cust_nm}] 매수 처리*",
                "blocks": blocks
            }
        except Exception as e:
            message = {
                "response_type": "ephemeral",
                "replace_original": True,
                "text": f"*[{market_name}] [{cust_nm}] 매수 처리 중 오류 발생* : {e}"
            } 
    
    elif action_id == "sell_action":
        selection = json.loads(payload["actions"][0]["value"])
        market_name = selection["market_name"]
        cust_nm = selection["cust_nm"]
        value = json.dumps({"market_name": market_name, "cust_nm": cust_nm})
        
        sell_buttons = []
        for text, action_id in [("전체 매도", "all_sell_action"), ("66% 매도", "66_sell_action"), ("절반 매도", "half_sell_action"), ("33% 매도", "33_sell_action"), ("25% 매도", "25_sell_action"), ("20% 매도", "20_sell_action"), ("현재가 매도", "direct_sell_action"), ("매도량 매도가", "custom_sell_action")]:
            value = json.dumps({"market_name": market_name, "cust_nm": cust_nm})
            sell_buttons.append({
                "type": "button",
                "text": { "type": "plain_text", "text": text },
                "value": value,
                "action_id": action_id
            })

        message = {
            "response_type": "ephemeral",
            "replace_original": True,
            "text": f"*[{market_name}] {cust_nm}*의 매도 방식을 선택하세요.",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "매도 방식을 선택하세요"
                    }
                },
                {
                    "type": "actions",
                    "elements": sell_buttons
                }
            ]
        }
    
    elif action_id == "all_sell_action":
        selection = json.loads(payload["actions"][0]["value"])
        market_name = selection["market_name"]
        cust_nm = selection["cust_nm"]
        value = json.dumps({"market_name": market_name, "cust_nm": cust_nm, "gubun": "all"})
        
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
                    "block_id": "price_input_block",
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "input_price",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "매도가를 입력해주세요"
                        }
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "매도가"
                    }
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "전체 매도",
                                "emoji": True
                            },
                            "value": value,
                            "action_id": "sell_proc"
                        }
                    ]
                }
            ]
        }
    
    elif action_id == "66_sell_action":
        selection = json.loads(payload["actions"][0]["value"])
        market_name = selection["market_name"]
        cust_nm = selection["cust_nm"]
        value = json.dumps({"market_name": market_name, "cust_nm": cust_nm, "gubun": "66"})
        
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
                    "block_id": "price_input_block",
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "input_price",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "매도가를 입력해주세요"
                        }
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "매도가"
                    }
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "66% 매도",
                                "emoji": True
                            },
                            "value": value,
                            "action_id": "sell_proc"
                        }
                    ]
                }
            ]
        } 
    
    elif action_id == "half_sell_action":
        selection = json.loads(payload["actions"][0]["value"])
        market_name = selection["market_name"]
        cust_nm = selection["cust_nm"]
        value = json.dumps({"market_name": market_name, "cust_nm": cust_nm, "gubun": "half"})
        
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
                    "block_id": "price_input_block",
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "input_price",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "매도가를 입력해주세요"
                        }
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "매도가"
                    }
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "절반 매도",
                                "emoji": True
                            },
                            "value": value,
                            "action_id": "sell_proc"
                        }
                    ]
                }
            ]
        }    
    
    elif action_id == "33_sell_action":
        selection = json.loads(payload["actions"][0]["value"])
        market_name = selection["market_name"]
        cust_nm = selection["cust_nm"]
        value = json.dumps({"market_name": market_name, "cust_nm": cust_nm, "gubun": "33"})
        
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
                    "block_id": "price_input_block",
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "input_price",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "매도가를 입력해주세요"
                        }
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "매도가"
                    }
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "33% 매도",
                                "emoji": True
                            },
                            "value": value,
                            "action_id": "sell_proc"
                        }
                    ]
                }
            ]
        }
        
    elif action_id == "25_sell_action":
        selection = json.loads(payload["actions"][0]["value"])
        market_name = selection["market_name"]
        cust_nm = selection["cust_nm"]
        value = json.dumps({"market_name": market_name, "cust_nm": cust_nm, "gubun": "25"})
        
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
                    "block_id": "price_input_block",
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "input_price",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "매도가를 입력해주세요"
                        }
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "매도가"
                    }
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "25% 매도",
                                "emoji": True
                            },
                            "value": value,
                            "action_id": "sell_proc"
                        }
                    ]
                }
            ]
        }
        
    elif action_id == "20_sell_action":
        selection = json.loads(payload["actions"][0]["value"])
        market_name = selection["market_name"]
        cust_nm = selection["cust_nm"]
        value = json.dumps({"market_name": market_name, "cust_nm": cust_nm, "gubun": "20"})
        
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
                    "block_id": "price_input_block",
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "input_price",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "매도가를 입력해주세요"
                        }
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "매도가"
                    }
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "20% 매도",
                                "emoji": True
                            },
                            "value": value,
                            "action_id": "sell_proc"
                        }
                    ]
                }
            ]
        }           
    
    elif action_id == "direct_sell_action":
        selection = json.loads(payload["actions"][0]["value"])
        market_name = selection["market_name"]
        cust_nm = selection["cust_nm"]
        value = json.dumps({"market_name": market_name, "cust_nm": cust_nm, "gubun": "direct"})
        
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
                    "block_id": "volumn_rate_input_block",
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "input_volumn_rate",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "매도비율(%)을 입력해주세요"
                        }
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "매도비율(%)"
                    }
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "현재가 매도",
                                "emoji": True
                            },
                            "value": value,
                            "action_id": "sell_proc"
                        }
                    ]
                }
            ]
        }   
        
    elif action_id == "custom_sell_action":
        selection = json.loads(payload["actions"][0]["value"])
        market_name = selection["market_name"]
        cust_nm = selection["cust_nm"]
        value = json.dumps({"market_name": market_name, "cust_nm": cust_nm, "gubun": "custom"})
        
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
                    "block_id": "price_input_block",
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "input_price",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "매도가를 입력해주세요"
                        }
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "매도가"
                    }
                },
                {
                    "type": "input",
                    "block_id": "volumn_input_block",
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "input_volumn",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "매도량를 입력해주세요"
                        }
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "매도량"
                    }
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "매도량 매도가 매도",
                                "emoji": True
                            },
                            "value": value,
                            "action_id": "sell_proc"
                        }
                    ]
                }
            ]
        }           
    
    elif action_id == "sell_proc":
        selection = json.loads(payload["actions"][0]["value"])
        market_name = selection["market_name"]
        cust_nm = selection["cust_nm"]
        gubun = selection.get("gubun")
        state_values = payload["state"]["values"]
        prd_nm = None
        price = None
        custom_volumn_rate = None
        custom_volumn = None

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
                    
                if "input_price" in block:
                    price_input = block["input_price"]["value"]
                    
                    # 유효성 검사
                    if not price_input:
                        raise ValueError("매도가를 입력해주세요.")
                    # 숫자인지 확인 (정수 또는 소수, 음수 불가)
                    if not re.fullmatch(r"\d+(\.\d{1,5})?", price_input):
                        raise ValueError("매도가는 0 이상의 숫자이며 소숫점 5자리까지만 입력 가능합니다.")

                    # 문자열을 float으로 변환
                    price = float(price_input)

                    # 0 이상의 값인지 확인
                    if price < 0:
                        raise ValueError("매도가는 0 이상의 숫자여야 합니다.")
                
                if "input_volumn_rate" in block:
                    volumn_input_rate = block["input_volumn_rate"]["value"]
                    
                    # 유효성 검사
                    if not volumn_input_rate:
                        raise ValueError("매도비율(%)을 입력해주세요.")
                    # 숫자인지 확인 (정수 또는 소수, 음수 불가)
                    if not re.fullmatch(r"\d+(\.\d{1,5})?", volumn_input_rate):
                        raise ValueError("매도비율(%)은 0 이상의 숫자이며 소숫점 5자리까지만 입력 가능합니다.")

                    # 문자열을 float으로 변환
                    custom_volumn_rate = float(volumn_input_rate)

                    # 0 이상의 값인지 확인
                    if custom_volumn_rate < 0:
                        raise ValueError("매도비율(%)은 0 이상의 숫자여야 합니다.")
                
                if "input_volumn" in block:
                    volumn_input = block["input_volumn"]["value"]
                    
                    # 유효성 검사
                    if not volumn_input:
                        raise ValueError("매도량을 입력해주세요.")
                    # 숫자인지 확인 (정수 또는 소수, 음수 불가)
                    if not re.fullmatch(r"\d+(\.\d{1,5})?", volumn_input):
                        raise ValueError("매도량은 0 이상의 숫자이며 소숫점 5자리까지만 입력 가능합니다.")

                    # 문자열을 float으로 변환
                    custom_volumn = float(volumn_input)

                    # 0 이상의 값인지 확인
                    if custom_volumn < 0:
                        raise ValueError("매도량은 0 이상의 숫자여야 합니다.")    

            # 매도 처리
            order_info = sell_proc(cust_nm=cust_nm, market_name=market_name, gubun=gubun, prd_nm=prd_nm, price=price, custom_volumn_rate=custom_volumn_rate, custom_volumn=custom_volumn)
            blocks = build_blocks(order_info, market_name, cust_nm)
            
            message = {
                "response_type": "ephemeral",
                "replace_original": True,
                "text": f"*[{market_name}] [{cust_nm}] 매도 처리*",
                "blocks": blocks
            }
        except Exception as e:
            message = {
                "response_type": "ephemeral",
                "replace_original": True,
                "text": f"*[{market_name}] [{cust_nm}] 매도 처리 중 오류 발생* : {e}"
            }  
    
    elif action_id == "order_open_action":
        selection = json.loads(payload["actions"][0]["value"])
        market_name = selection["market_name"]
        cust_nm = selection["cust_nm"]
        
        try:
            # 대기주문내역 조회
            order_list = get_order_open(cust_nm=cust_nm, market_name=market_name)
            blocks = build_blocks(order_list, market_name, cust_nm)
            
            message = {
                "response_type": "ephemeral",
                "replace_original": True,
                "text": f"*[{market_name}] [{cust_nm}] 대기주문내역*",
                "blocks": blocks
            }
        except Exception as e:
            message = {
                "response_type": "ephemeral",
                "replace_original": True,
                "text": f"*[{market_name}] [{cust_nm}] 대기주문내역 조회 중 오류 발생* : {e}"
            }
        
    elif action_id == "order_update_action":
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
                    "block_id": "ord_no_input_block",
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "input_ord_no",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "주문번호를 입력해주세요"
                        }
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "주문번호"
                    }
                },
                {
                    "type": "input",
                    "block_id": "price_input_block",
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "input_price",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "매매가를 입력해주세요"
                        }
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "매매가"
                    }
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "주문 정정",
                                "emoji": True
                            },
                            "value": value,
                            "action_id": "order_update_proc"
                        }
                    ]
                }
            ]
        }
    
    elif action_id == "order_update_proc":
        selection = json.loads(payload["actions"][0]["value"])
        market_name = selection["market_name"]
        cust_nm = selection["cust_nm"]
        state_values = payload["state"]["values"]
        order_no = None
        price = None

        try:
            for block_id, block in state_values.items():
                if "input_ord_no" in block:
                    order_no = block["input_ord_no"]["value"]
                    
                    # 유효성 검사
                    if not order_no:
                        raise ValueError("주문번호를 입력해주세요.")
                    
                if "input_price" in block:
                    price_input = block["input_price"]["value"]
                    
                    # 유효성 검사
                    if not price_input:
                        raise ValueError("매매가를 입력해주세요.")
                    # 숫자인지 확인 (정수 또는 소수, 음수 불가)
                    if not re.fullmatch(r"\d+(\.\d{1,5})?", price_input):
                        raise ValueError("매매가는 0 이상의 숫자이며 소숫점 5자리까지만 입력 가능합니다.")

                    # 문자열을 float으로 변환
                    price = float(price_input)

                    # 0 이상의 값인지 확인
                    if price < 0:
                        raise ValueError("매매가는 0 이상의 숫자여야 합니다.")
            
            # 주문 취소 후 재주문
            order_update_info = order_update(cust_nm=cust_nm, market_name=market_name, order_no=order_no, price=price)
            blocks = build_blocks(order_update_info, market_name, cust_nm)
            
            message = {
                "response_type": "ephemeral",
                "replace_original": True,
                "text": f"*[{market_name}] [{cust_nm}] 주문 취소 후 재주문*",
                "blocks": blocks
            }
        except Exception as e:
            message = {
                "response_type": "ephemeral",
                "replace_original": True,
                "text": f"*[{market_name}] [{cust_nm}] 주문 취소 후 재주문 중 오류 발생* : {e}"
            }
    
    elif action_id == "order_cancel_action":
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
                    "block_id": "ord_no_input_block",
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "input_ord_no",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "주문번호를 입력해주세요"
                        }
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "주문번호"
                    }
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "주문 취소",
                                "emoji": True
                            },
                            "value": value,
                            "action_id": "order_cancel_proc"
                        }
                    ]
                }
            ]
        }
        
    elif action_id == "order_cancel_proc":
        selection = json.loads(payload["actions"][0]["value"])
        market_name = selection["market_name"]
        cust_nm = selection["cust_nm"]
        order_no = payload["state"]["values"]["ord_no_input_block"]["input_ord_no"]["value"]
            
        try:
            if not order_no:
                raise ValueError("주문번호를 입력해주세요.")
            
            # 주문 취소 접수
            order_cancel_info = order_cancel(cust_nm=cust_nm, market_name=market_name, order_no=order_no)
            
            message = {
                "response_type": "ephemeral",
                "replace_original": True,
                "text": f"*[{market_name}] [{cust_nm}] 주문 취소 접수*\n{order_cancel_info}"
            }
        except Exception as e:
            message = {
                "response_type": "ephemeral",
                "replace_original": True,
                "text": f"*[{market_name}] [{cust_nm}] 주문 취소 접수 중 오류 발생* : {e}"
            }
            
    elif action_id == "order_close_action":
        selection = json.loads(payload["actions"][0]["value"])
        market_name = selection["market_name"]
        cust_nm = selection["cust_nm"]
        
        # Base64 인코딩된 value 생성
        encoded_value = encode_value({
            "market_name": market_name,
            "cust_nm": cust_nm
        })
        
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
                    "block_id": "ord_no_input_block",
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "input_ord_no",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "주문번호를 입력해주세요"
                        }
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "주문번호"
                    }
                },
                {
                    "type": "input",
                    "block_id": "start_dt_input_block",
                    "element": {
                        "type": "datepicker",
                        "action_id": "input_start_dt",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "주문조회 시작일을 선택해주세요"
                        }
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "주문조회 시작일"
                    }
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "종료주문 조회",
                                "emoji": True
                            },
                            "value": encoded_value,
                            "action_id": "order_close_proc"
                        }
                    ]
                }
            ]
        }
    
    elif action_id == "order_close_proc":
        try:
            decoded = decode_value(payload["actions"][0]["value"])
            market_name = decoded.get("market_name")
            cust_nm = decoded.get("cust_nm")
            order_no_from_button = decoded.get("order_no")

            state_values = payload.get("state", {}).get("values", {})

            prd_nm = None
            order_no = None
            start_dt = None

            for block in state_values.values():
                if not isinstance(block, dict):
                    continue

                prd_nm_val = block.get("input_prd_nm", {}).get("value")
                if prd_nm_val:
                    if not re.fullmatch(r'[A-Za-z]+', prd_nm_val):
                        raise ValueError("상품명은 영문 알파벳만 입력 가능합니다.")
                    prd_nm = prd_nm_val.upper()

                ord_no_val = block.get("input_ord_no", {}).get("value")
                if ord_no_val:
                    order_no = ord_no_val

                start_dt_val = block.get("input_start_dt", {}).get("selected_date")
                if start_dt_val:
                    start_dt = start_dt_val.replace("-", "")

            # fallback: 입력이 없으면 버튼에서 받은 주문번호 사용
            order_no = order_no or order_no_from_button
            
            if not start_dt or start_dt == "_" or not isinstance(start_dt, str):
                start_dt = (datetime.today() - timedelta(days=30)).strftime("%Y%m%d")

            # 종료 주문 데이터 조회
            order_close_info = get_order_close(
                cust_nm=cust_nm,
                market_name=market_name,
                prd_nm=prd_nm,
                order_no=order_no,
                start_dt=start_dt
            )

            # 블록 생성
            blocks = build_blocks(
                text_lines=order_close_info,
                market_name=market_name,
                cust_nm=cust_nm,
                prd_nm=prd_nm,
                order_no=order_no,
                start_dt=start_dt,
                page=1,
                page_size=15
            )

            message = {
                "response_type": "ephemeral",
                "replace_original": True,
                "text": f"*[{market_name}] [{cust_nm}] 종료 주문 조회*",
                "blocks": blocks
            }
        except Exception as e:
            message = {
                "response_type": "ephemeral",
                "replace_original": True,
                "text": f"*[{market_name}] [{cust_nm}] 종료 주문 조회 중 오류 발생* : {e}"
            }

    elif action_id == "paginate_order_close":
        try:
            decoded = decode_value(payload["actions"][0]["value"])

            market_name = decoded.get("market_name")
            cust_nm = decoded.get("cust_nm")
            prd_nm = decoded.get("prd_nm")
            order_no = decoded.get("order_no")
            start_dt = decoded.get("start_dt")
            page = int(decoded.get("page", 1))
            page_size = int(decoded.get("page_size", 15))

            def none_if_placeholder(val):
                return None if val in ("_", "-", "") else val

            prd_nm = none_if_placeholder(prd_nm)
            order_no = none_if_placeholder(order_no)
            start_dt = none_if_placeholder(start_dt)

            order_close_info = get_order_close(
                cust_nm=cust_nm,
                market_name=market_name,
                prd_nm=prd_nm,
                order_no=order_no,
                start_dt=start_dt
            )

            blocks = build_blocks(
                text_lines=order_close_info,
                market_name=market_name,
                cust_nm=cust_nm,
                prd_nm=prd_nm,
                order_no=order_no,
                start_dt=start_dt,
                page=page,
                page_size=page_size
            )

            message = {
                "response_type": "ephemeral",
                "replace_original": True,
                "text": f"*[{market_name}] [{cust_nm}] 종료 주문 조회 (Page {page})*",
                "blocks": blocks
            }

        except Exception as e:
            message = {
                "response_type": "ephemeral",
                "replace_original": True,
                "text": f"페이지 이동 중 오류 발생: {e}"
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
    except requests.exceptions.HTTPError as e:
        print("Slack 응답 실패:", e.response.status_code, "-", e.response.text)
        print("전송된 message:", json.dumps(message, ensure_ascii=False, indent=2))    

    return JSONResponse(content="") 

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