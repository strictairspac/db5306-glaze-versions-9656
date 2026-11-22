from fastapi import APIRouter, Depends
from sqlalchemy import text

from src.config.settings import cache, engine
from src.controllers.workflow import download
from src.models.tables import audit, cases, evidence, runs
from src.schemas.workflow import CaseForm, LoginForm, LookupForm, RenumberForm, ReviewForm
from src.services import domain, workflow
from src.services.security import current_user, login, operator, reviewer

router = APIRouter()


@router.get('/health')
def health():
    with engine.connect() as conn:
        conn.execute(text('SELECT 1')).scalar_one()
    cache.ping()
    return {'ok': True}


@router.post('/login')
def sign_in(form: LoginForm):
    return login(form.username, form.password)


@router.get('/spec')
def spec(_=Depends(current_user)):
    return domain.SPEC


@router.get('/cases')
def list_cases(_=Depends(current_user)):
    return workflow.recent(cases)


@router.post('/cases', status_code=201)
def create_case(form: CaseForm, actor=Depends(operator)):
    return workflow.make_case(form.title, form.payload, actor)


@router.post('/cases/{case_id}/renumbers/preview')
def preview_renumber(case_id: int, form: RenumberForm, _=Depends(operator)):
    return workflow.preview_renumber(case_id, form.commit_seq, form.changes)


@router.post('/cases/{case_id}/renumbers', status_code=201)
def register_renumber(case_id: int, form: RenumberForm, actor=Depends(operator)):
    return workflow.register_renumber(case_id, form.commit_seq, form.changes, actor)


@router.post('/cases/{case_id}/lookups/preview')
def preview_lookup(case_id: int, form: LookupForm, _=Depends(current_user)):
    return workflow.preview_lookup(case_id, form.glaze_no, form.read_seq)


@router.get('/runs')
def list_runs(_=Depends(current_user)):
    return workflow.recent(runs)


@router.post('/cases/{case_id}/runs', status_code=202)
def start_run(case_id: int, actor=Depends(operator)):
    return workflow.enqueue(case_id, actor)


@router.get('/evidence')
def list_evidence(_=Depends(current_user)):
    return workflow.recent(evidence)


@router.get('/evidence/{identity}/download')
def export_evidence(identity: int, _=Depends(current_user)):
    return download(identity)


@router.post('/evidence/{identity}/review')
def review_evidence(identity: int, form: ReviewForm, actor=Depends(reviewer)):
    return workflow.review(identity, form.note, actor)


@router.get('/audit')
def list_audit(_=Depends(current_user)):
    return workflow.recent(audit)
