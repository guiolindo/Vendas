from flask import Blueprint, render_template

from .. import clock
from ..services import dashboard
from .helpers import db

bp = Blueprint("dashboard", __name__)


@bp.get("/")
def index():
    return render_template("dashboard.html", d=dashboard.build(db(), clock.today()))
