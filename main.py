from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import json
import requests
from routers.trade_proc import get_balance
# from routers import auth as auth_router
from routers import cust_mng as cust_mng_router
from routers import trade_mng as trade_mng_router
import click
from montecarlo import montecarlo as montecarlo_

app = FastAPI()

# app.include_router(auth_router.router, prefix="/api/auth", tags=["auth"])
app.include_router(cust_mng_router.router, prefix="/api/cust_mng", tags=["cust_mng"])
app.include_router(trade_mng_router.router, prefix="/api/trade_mng", tags=["trade_mng"])

@app.get("/")
def read_root():
    return {"message": "Hello, World!"}

# Slash Command 처리
@app.post("/slack/command")
async def slack_command(request: Request):
    form = await request.form()
    command = form.get("command")
    text = form.get("text")
    user_id = form.get("user_id")

    # Slack으로 인터랙티브 버튼 리턴
    return JSONResponse(
        content={
            "response_type": "ephemeral",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"안녕하세요 <@{user_id}>님! 무엇을 하시겠습니까?"
                    }
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "잔고정보",
                            },
                            "value": "balance",
                            "action_id": "balance_action"
                        },
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "매수",
                            },
                            "value": "buy",
                            "action_id": "buy_action"
                        },
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "매도",
                            },
                            "value": "sell",
                            "action_id": "sell_action"
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
    user_id = payload["user"]["id"]
    response_url = payload["response_url"]

    message = {}

    if action_id == "balance_action":
        # 거래소 선택 버튼 응답
        message = {
            "response_type": "ephemeral",
            "replace_original": True,
            "text": "거래소 선택",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "어느 거래소의 잔고를 확인하시겠습니까?"
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
                                "emoji": True
                            },
                            "value": "UPBIT",
                            "action_id": "select_upbit"
                        },
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "BITHUMB",
                                "emoji": True
                            },
                            "value": "BITHUMB",
                            "action_id": "select_bithumb"
                        }
                    ]
                }
            ]
        }

    elif action_id in ["select_upbit", "select_bithumb"]:
        # 고객명 입력 모달 응답
        market_name = payload["actions"][0]["value"]

        message = {
            "response_type": "ephemeral",
            "replace_original": True,
            "blocks": [
                {
                    "type": "input",
                    "block_id": "cust_input_block",
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "input_cust_nm",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "고객명을 입력해주세요"
                        }
                    },
                    "label": {
                        "type": "plain_text",
                        "text": f"{market_name} 고객명 입력"
                    }
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "조회",
                                "emoji": True
                            },
                            "value": market_name,
                            "action_id": "confirm_balance"
                        }
                    ]
                }
            ]
        }

    elif action_id == "confirm_balance":
        # 고객명 입력받아 잔고 조회
        market_name = payload["actions"][0]["value"]
        cust_nm = payload["state"]["values"]["cust_input_block"]["input_cust_nm"]["value"]

        try:
            balance_result = get_balance(cust_nm, market_name)
            result_text = f"<@{user_id}>님의 {market_name} 잔고:\n{balance_result}"
        except Exception as e:
            result_text = f"잔고 조회 중 오류 발생: {e}"

        message = {
            "response_type": "ephemeral",
            "replace_original": True,
            "text": result_text
        }

    else:
        # 기타 예외
        message = {
            "response_type": "ephemeral",
            "text": f"알 수 없는 액션입니다: {action_id}"
        }

    # Slack 응답 전송
    try:
        res = requests.post(response_url, json=message)
        if not res.ok:
            print(f"Slack 응답 실패: {res.status_code} - {res.text}")
    except requests.exceptions.RequestException as e:
        print(f"Slack 전송 실패: {e}")

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