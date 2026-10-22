"""One indexed interpretation of maintained capacity, calendar and switch inputs."""
from datetime import date
from .input_models import InputSnapshot


class InputIndex:
    def __init__(self, inputs: InputSnapshot):
        self.models = {row.code: row for row in inputs.masters.models}
        self.recipes = {row.code: row for row in inputs.masters.recipes}
        self.lines = {row.code: row for row in inputs.masters.lines}
        self.capabilities = {(row.recipe_code, row.line_code): row for row in inputs.masters.capabilities}
        self.capacities = {(row.month, row.recipe_code, row.line_code): row for row in inputs.parameters.capacities}
        self.line_yields = {(row.month, getattr(row, 'model_code', None)): row for row in inputs.parameters.line_yields}
        self.model_yields = {(row.month, row.model_code): row for row in inputs.parameters.model_yields}
        self.cycle_times = {row.model_code: row for row in inputs.parameters.cycle_times}
        self.switch_durations = {(row.from_recipe, row.to_recipe): row for row in inputs.parameters.switch_durations}
        self.holidays = {row.date: row.holiday for row in inputs.calendar.holidays}
        self.openings = {(row.date, row.line_code): row.enabled for row in inputs.calendar.opening_exceptions}
        self.maintenance = {(row.date, row.line_code): row.hours for row in inputs.calendar.maintenance}
        self.experiments = {(row.date, row.line_code): row.hours for row in inputs.calendar.experiments}

    def capacity(self, month: str, recipe_code: str, line_code: str):
        row = self.capacities.get((month, recipe_code, line_code))
        permission = self.capabilities.get((recipe_code, line_code))
        state = 'forbidden' if permission and not permission.allowed else 'missing' if row is None else 'zero' if row.pieces == 0 else 'known'
        return {'state': state, 'pieces': row.pieces if row else 0}

    def switch(self, from_recipe: str | None, to_recipe: str):
        if from_recipe is None:
            return {'state': 'unknown_initial', 'hours': '0'}
        if from_recipe == to_recipe:
            return {'state': 'same_recipe', 'hours': '0'}
        row = self.switch_durations.get((from_recipe, to_recipe))
        return {'state': 'known' if row else 'default_missing', 'hours': row.hours if row else '12'}

    def day(self, production_date: date | None, line_code: str):
        key = (production_date, line_code)
        holiday = self.holidays.get(production_date, False)
        available = 0 if holiday and not self.openings.get(key, False) else max(24 - self.maintenance.get(key, 0) - self.experiments.get(key, 0), 0)
        return {'available_hours': available, 'output_holiday': holiday}

    def yields(self, month: str, model_code: str):
        return {'line_yield': self.line_yields.get((month, model_code)) or self.line_yields.get((month, None)),
                'model_yield': self.model_yields.get((month, model_code))}

    def critical_parameters(self, month: str, model_code: str):
        def parameter(row, field):
            return {'state': 'known' if row else 'missing', field: getattr(row, field) if row else None}
        yields = self.yields(month, model_code)
        return {'line_yield': parameter(yields['line_yield'], 'percent'),
                'model_yield': parameter(yields['model_yield'], 'percent'),
                'cycle_time': parameter(self.cycle_times.get(model_code), 'days')}

    def availability(self, month: str, model_code: str, line_code: str, production_date: date | None, from_recipe: str | None):
        model = self.models[model_code]
        if line_code not in self.lines or from_recipe is not None and from_recipe not in self.recipes:
            raise KeyError
        return {'capacity': self.capacity(month, model.recipe_code, line_code),
                **self.critical_parameters(month, model_code), 'switch': self.switch(from_recipe, model.recipe_code),
                **self.day(production_date, line_code)}
