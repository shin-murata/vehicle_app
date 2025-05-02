from flask_wtf import FlaskForm
from wtforms import SelectField, StringField, IntegerField, DateField
from wtforms.validators import DataRequired, Optional

class EstimationForm(FlaskForm):
    # ↓ 追加：マスターから選ぶ（初期は空）
    maker_select = SelectField('メーカー（選択）', choices=[], validate_choice=False)
    # ↓ 追加：手入力欄
    maker_manual = StringField('メーカー（手入力）')
    
    car_name = StringField('車名', validators=[DataRequired()])
    model_code = StringField('型式', validators=[DataRequired()])
    estimate_price = IntegerField('見積金額', validators=[DataRequired()])

    # ✅ クライアント関連（依頼元）
    client_select = SelectField('依頼元（選択）', choices=[], validate_choice=False)
    client_manual = StringField('依頼元（手入力）', validators=[Optional()])

    sale_price = IntegerField('販売価格', validators=[Optional()])
    
    # ✅ 追加：buyer用の選択肢と手入力欄
    buyer_select = SelectField('販売先（選択）', choices=[], validate_choice=False)
    buyer_manual = StringField('販売先（手入力）', validators=[Optional()])
    
    sold_at = DateField('販売日', format='%Y-%m-%d', validators=[Optional()])
    note = StringField('備考', validators=[Optional()])
