from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.db import close_pool, get_pool
from app.routers import api

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "templates"))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    get_pool()
    yield
    close_pool()


app = FastAPI(title="CA Workbench - Financial Statements", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
app.include_router(api.router)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)                     # no icon: answer quietly instead of logging a 404


@app.get("/pack", response_class=HTMLResponse, include_in_schema=False)
def page_pack(request: Request):
    return templates.TemplateResponse(request, "pack.html", {"page": "pack"})


@app.get("/audit-trail", response_class=HTMLResponse, include_in_schema=False)
def page_audit_trail(request: Request):
    return templates.TemplateResponse(request, "audit_log.html", {"page": "audit"})


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def page_trial_balance(request: Request):
    return templates.TemplateResponse(request, "tb.html", {"page": "tb"})


@app.get("/adjustments", response_class=HTMLResponse, include_in_schema=False)
def page_adjustments(request: Request):
    return templates.TemplateResponse(request, "adjustments.html", {"page": "adjustments"})


@app.get("/working-papers", response_class=HTMLResponse, include_in_schema=False)
def page_working_papers(request: Request):
    return templates.TemplateResponse(request, "working_papers.html", {"page": "wp"})


@app.get("/issues", response_class=HTMLResponse, include_in_schema=False)
def page_issues(request: Request):
    return templates.TemplateResponse(request, "issues.html", {"page": "issues"})


@app.get("/lfar", response_class=HTMLResponse, include_in_schema=False)
def page_lfar(request: Request):
    return templates.TemplateResponse(request, "lfar.html", {"page": "lfar"})


@app.get("/reconciliation", response_class=HTMLResponse, include_in_schema=False)
def page_recon(request: Request):
    return templates.TemplateResponse(request, "reconciliation.html", {"page": "recon"})


@app.get("/gstr9c", response_class=HTMLResponse, include_in_schema=False)
def page_gstr9c(request: Request):
    return templates.TemplateResponse(request, "gstr9c.html", {"page": "gstr9c"})


@app.get("/tds-tcs", response_class=HTMLResponse, include_in_schema=False)
def page_tds(request: Request):
    return templates.TemplateResponse(request, "tds_tcs.html", {"page": "tds"})


@app.get("/fixed-deposits", response_class=HTMLResponse, include_in_schema=False)
def page_fd(request: Request):
    return templates.TemplateResponse(request, "fixed_deposits.html", {"page": "fd"})


@app.get("/materiality", response_class=HTMLResponse, include_in_schema=False)
def page_materiality(request: Request):
    return templates.TemplateResponse(request, "materiality.html", {"page": "materiality"})


@app.get("/udin", response_class=HTMLResponse, include_in_schema=False)
def page_udin(request: Request):
    return templates.TemplateResponse(request, "udin.html", {"page": "udin"})


@app.get("/signoff", response_class=HTMLResponse, include_in_schema=False)
def page_signoff(request: Request):
    return templates.TemplateResponse(request, "signoff.html", {"page": "signoff"})


@app.get("/notes", response_class=HTMLResponse, include_in_schema=False)
def page_notes(request: Request):
    return templates.TemplateResponse(request, "notes.html", {"page": "notes"})


@app.get("/ratios", response_class=HTMLResponse, include_in_schema=False)
def page_ratios(request: Request):
    return templates.TemplateResponse(request, "ratios.html", {"page": "ratios"})


@app.get("/checks", response_class=HTMLResponse, include_in_schema=False)
def page_checks(request: Request):
    return templates.TemplateResponse(request, "checks.html", {"page": "checks"})


@app.get("/balance-sheet", response_class=HTMLResponse, include_in_schema=False)
def page_balance_sheet(request: Request):
    return templates.TemplateResponse(request, "balance_sheet.html", {"page": "bs"})


@app.get("/pnl", response_class=HTMLResponse, include_in_schema=False)
def page_pnl(request: Request):
    return templates.TemplateResponse(request, "pnl.html", {"page": "pnl"})


@app.get("/mapping", response_class=HTMLResponse, include_in_schema=False)
def page_mapping(request: Request):
    return templates.TemplateResponse(request, "mapping.html", {"page": "mapping"})
