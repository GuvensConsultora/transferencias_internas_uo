# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError

class CashTransfer(models.Model):
    _name = 'cash.transfer'
    _description = 'Transferencia de Efectivo'
    _order = 'date desc, id desc'

    date = fields.Date(string='Fecha', default=fields.Date.context_today, required=True)
    company_id = fields.Many2one('res.company', string='Compañía', required=True, default=lambda self: self.env.company)

    # Dominios: sólo diarios de efectivo y de la compañía; el from se filtrará por OU en onchange
    journal_id_from = fields.Many2one(
        'account.journal', string='Desde', required=True,
        domain="[('type','=','cash'), ('company_id','=', company_id)]"
    )
    journal_id_to = fields.Many2one(
        'account.journal', string='Hacia', required=True,
        domain="[('type','=','cash'), ('company_id','=', company_id)]"
    )

    amount = fields.Monetary(string='Importe', required=True)
    currency_id = fields.Many2one('res.currency', string='Moneda', required=True,
                                  default=lambda self: self.env.company.currency_id)
    state = fields.Selection([('draft', 'Borrador'), ('validated', 'Validado')], default='draft', tracking=True)

    # =========================
    #  AUTOCOMPLETADOS / DOMINIOS DINÁMICOS
    # =========================

    @api.model
    def _get_user_default_ou(self):
        """(supuesto) OCA: default_operating_unit_id en res.users."""
        user = self.env.user
        return getattr(user, 'default_operating_unit_id', False)

    @api.model
    def _find_cash_journal_by_ou(self, company, ou):
        """Busca un diario de efectivo de la OU dada."""
        Journal = self.env['account.journal']
        domain = [
            ('type', '=', 'cash'),
            ('company_id', '=', company.id),
        ]
        if ou:
            # (supuesto) campo operating_unit_id en account.journal
            domain.append(('operating_unit_id', '=', ou.id))
        return Journal.search(domain, limit=1, order='id asc')

    @api.model
    def _get_central_cash_journal(self, company):
        """
        Obtiene el Diario de Efectivo 'Central'.
        1) Si está seteado en ir.config_parameter: cash_transfer.central_journal_id
        2) (fallback) Busca por OU con nombre/código 'Central' (supuesto)
        """
        Journal = self.env['account.journal']
        ICP = self.env['ir.config_parameter'].sudo()
        central_id = int(ICP.get_param('cash_transfer.central_journal_id', default='0') or 0)
        if central_id:
            j = Journal.browse(central_id).exists()
            if j and j.company_id == company and j.type == 'cash':
                return j

        # Fallback por OU 'Central' (supuesto de naming)
        OU = self.env['operating.unit']
        central_ou = OU.search([('company_id', '=', company.id), '|', ('name', 'ilike', 'Central'), ('code', '=', 'CENTRAL')], limit=1)
        if central_ou:
            j = Journal.search([('type', '=', 'cash'), ('company_id', '=', company.id),
                                ('operating_unit_id', '=', central_ou.id)], limit=1)
            if j:
                return j
        return Journal.browse(False)

    @api.model
    def default_get(self, fields_list):
        """Setea por defecto: Desde = efectivo de OU del usuario; Hacia = efectivo central."""
        res = super().default_get(fields_list)
        company = self.env.company
        user_ou = self._get_user_default_ou()
        j_from = self._find_cash_journal_by_ou(company, user_ou)
        j_to = self._get_central_cash_journal(company)

        if 'journal_id_from' in fields_list and j_from:
            res['journal_id_from'] = j_from.id
        if 'journal_id_to' in fields_list and j_to:
            res['journal_id_to'] = j_to.id
        return res

    @api.onchange('company_id')
    def _onchange_company_id_set_journals(self):
        """Al cambiar compañía, re-propone los diarios en base a OU usuario y Central."""
        for rec in self:
            if not rec.company_id:
                continue
            user_ou = rec._get_user_default_ou()
            rec.journal_id_from = rec._find_cash_journal_by_ou(rec.company_id, user_ou)
            rec.journal_id_to = rec._get_central_cash_journal(rec.company_id)

    @api.onchange('journal_id_from')
    def _onchange_from_enforce_ou(self):
        """Si el usuario tiene OU, fuerza que el 'Desde' pertenezca a esa OU (si existe)."""
        for rec in self:
            user_ou = rec._get_user_default_ou()
            if rec.company_id and user_ou:
                if rec.journal_id_from and getattr(rec.journal_id_from, 'operating_unit_id', False) != user_ou:
                    # Reajustar automáticamente al de la OU del usuario si hay uno
                    j = rec._find_cash_journal_by_ou(rec.company_id, user_ou)
                    if j:
                        rec.journal_id_from = j

    @api.onchange('journal_id_to')
    def _onchange_to_enforce_central(self):
        """Si existe diario Central, mantenerlo como destino."""
        for rec in self:
            if rec.company_id:
                central = rec._get_central_cash_journal(rec.company_id)
                if central:
                    rec.journal_id_to = central

    # =========================
    #  LÓGICA EXISTENTE + GARANTÍA CENTRAL
    # =========================

    def _main_account(self, journal):
        if not journal:
            return False
        return journal.default_account_id or journal.payment_debit_account_id or journal.payment_credit_account_id

    def action_validate(self):
        for rec in self:
            if rec.amount <= 0:
                raise UserError(_('El importe debe ser mayor a cero.'))
            if not rec.journal_id_from or not rec.journal_id_to:
                raise UserError(_('Debe seleccionar ambos diarios.'))
            if rec.journal_id_from == rec.journal_id_to:
                raise UserError(_('El diario de origen y destino no pueden ser el mismo.'))

            # Enforce: destino debe ser Central si está configurado
            central = rec._get_central_cash_journal(rec.company_id)
            if central and rec.journal_id_to != central:
                raise UserError(_('El diario destino debe ser el Diario Central configurado.'))

            acc_from = rec._main_account(rec.journal_id_from)
            acc_to = rec._main_account(rec.journal_id_to)
            if not acc_from or not acc_to:
                raise UserError(_('Falta configurar la cuenta principal en uno de los diarios.'))

            company_currency = rec.company_id.currency_id
            transfer_currency = rec.currency_id

            if transfer_currency != company_currency:
                line_in = {
                    'name': _('Entrada a %s') % rec.journal_id_to.name,
                    'account_id': acc_to.id,
                    'debit': rec.amount,
                    'credit': 0.0,
                    'currency_id': transfer_currency.id,
                    'company_id': rec.company_id.id,
                }
                line_out = {
                    'name': _('Salida de %s') % rec.journal_id_from.name,
                    'account_id': acc_from.id,
                    'debit': 0.0,
                    'credit': rec.amount,
                    'currency_id': transfer_currency.id,
                    'company_id': rec.company_id.id,
                }
            else:
                line_in = {
                    'name': _('Entrada a %s') % rec.journal_id_to.name,
                    'account_id': acc_to.id,
                    'debit': rec.amount,
                    'credit': 0.0,
                    'currency_id': company_currency.id,
                    'company_id': rec.company_id.id,
                }
                line_out = {
                    'name': _('Salida de %s') % rec.journal_id_from.name,
                    'account_id': acc_from.id,
                    'debit': 0.0,
                    'credit': rec.amount,
                    'currency_id': company_currency.id,
                    'company_id': rec.company_id.id,
                }

            move_vals = {
                'date': rec.date,
                'journal_id': rec.journal_id_to.id,
                'ref': _('Transferencia de efectivo #%s') % rec.id,
                'line_ids': [(0, 0, line_in), (0, 0, line_out)],
                'company_id': rec.company_id.id,
            }
            move = self.env['account.move'].create(move_vals)
            move.action_post()
            rec.state = 'validated'
        return True
