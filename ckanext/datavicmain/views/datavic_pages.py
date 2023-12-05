from flask import Blueprint
import ckanext.pages.blueprint as pages_blueprint
from ckanext.datavicmain import config


datavicpages = Blueprint('datavicpages', __name__)

datavicpages.add_url_rule(f"/{config.get_pages_base_url()}", view_func=pages_blueprint.index, endpoint="pages_index")
datavicpages.add_url_rule(f"/{config.get_pages_base_url()}/<page>", view_func=pages_blueprint.show)
