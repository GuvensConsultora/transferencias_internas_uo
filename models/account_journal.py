# -*- coding: utf-8 -*-
from odoo import fields, models


class AccountJournal(models.Model):
    _inherit = 'account.journal'

    is_central_cash_journal = fields.Boolean(
        string='Diario Central',
        help='Marcar este diario como el Diario Central para '
             'las transferencias de efectivo entre sucursales.',
        copy=False,
    )
