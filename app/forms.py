from flask_wtf import FlaskForm
from wtforms import StringField, IntegerField, DateField
from wtforms.validators import DataRequired, Optional

class EstimationForm(FlaskForm):
    maker = StringField('メーカー', validators=[DataRequired()])
    car_name = StringField('車名', validators=[DataRequired()])
    model_code = StringField('型式', validators=[DataRequired()])
    estimate_price = IntegerField('見積金額', validators=[DataRequired()])

    owner = StringField('持ち主', validators=[Optional()])
    sale_price = IntegerField('販売価格', validators=[Optional()])
    buyer = StringField('販売先', validators=[Optional()])
    sold_at = DateField('販売日', format='%Y-%m-%d', validators=[Optional()])
    note = StringField('備考', validators=[Optional()])
