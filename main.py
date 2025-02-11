from fastapi import FastAPI
from sqlalchemy import create_engine
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