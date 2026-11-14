"""Public ingredient endpoints; register this router before the static mount."""
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .database import connect
from .ingredient_matching import (
    SQLITE_MAX_INTEGER, UnknownIngredientsError, find_ingredients, match_recipes,
)

router = APIRouter(prefix='/api/v1', tags=['ingredients'])
PositiveInteger = Annotated[int, Field(strict=True, ge=1, le=SQLITE_MAX_INTEGER)]


class MatchInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    ingredient_ids: list[PositiveInteger] = Field(max_length=100)
    page: PositiveInteger = 1
    page_size: Annotated[int, Field(strict=True, ge=1, le=100)] = 24


class Ingredient(BaseModel):
    id: int
    name: str


class IngredientPage(BaseModel):
    items: list[Ingredient]
    total: int
    page: int
    page_size: int


class RecipeMatch(BaseModel):
    id: int
    name: str
    required_count: int
    matched_count: int
    missing_count: int
    coverage: float
    matched_ingredients: list[Ingredient]
    missing_ingredients: list[Ingredient]


class MatchPage(BaseModel):
    ingredient_ids: list[int]
    items: list[RecipeMatch]
    total: int
    page: int
    page_size: int


def validate_offset(page, page_size):
    if (page - 1) * page_size > SQLITE_MAX_INTEGER:
        raise HTTPException(422, '分页偏移超出 SQLite 整数范围')


@router.get('/ingredients', response_model=IngredientPage)
def ingredients(q: str = Query('', max_length=100),
                page: int = Query(1, ge=1, le=SQLITE_MAX_INTEGER),
                page_size: int = Query(20, ge=1, le=100)):
    validate_offset(page, page_size)
    with connect() as db:
        db.execute('BEGIN')
        return find_ingredients(db, q, page, page_size)


@router.post('/recipe-matches', response_model=MatchPage)
def recipe_matches(data: MatchInput):
    validate_offset(data.page, data.page_size)
    if not data.ingredient_ids:
        raise HTTPException(422, '请至少选择一种食材')
    with connect() as db:
        db.execute('BEGIN')
        try:
            return match_recipes(db, data.ingredient_ids, data.page, data.page_size)
        except UnknownIngredientsError as error:
            raise HTTPException(422, str(error)) from error
