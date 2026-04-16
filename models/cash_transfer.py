# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class CashTransfer(models.Model):
    _name = 'cash.transfer'
    _inherit = ['mail.thread']
    _description = 'Transferencia de Efectivo'
    _order = 'date desc, id desc'

    date = fields.Date(
        string='Fecha',
        default=fields.Date.context_today,
        required=True,
    )
    company_id = fields.Many2one(
        'res.company', string='Compañía',
        required=True,
        default=lambda self: self.env.company,
    )
    journal_id_from = fields.Many2one(
        'account.journal', string='Desde', required=True,
        domain="[('type', 'in', ('cash', 'bank')), "
               "('company_id', '=', company_id)]",
    )
    journal_id_to = fields.Many2one(
        'account.journal', string='Hacia', required=True,
        domain="[('type', 'in', ('cash', 'bank')), "
               "('company_id', '=', company_id)]",
    )
    amount = fields.Monetary(string='Importe', required=True)
    currency_id = fields.Many2one(
        'res.currency', string='Moneda', required=True,
        default=lambda self: self.env.company.currency_id,
    )
    state = fields.Selection([
        ('draft', 'Borrador'),
        ('validated', 'Validado'),
        ('cancelled', 'Cancelado'),
    ], default='draft', string='Estado', tracking=True)
    move_id = fields.Many2one(
        'account.move', string='Asiento contable',
        readonly=True, copy=False,
    )
    allowed_journal_from_ids = fields.Many2many(
        'account.journal',
        compute='_compute_allowed_journals',
        string='Diarios de origen permitidos',
    )
    allowed_journal_to_ids = fields.Many2many(
        'account.journal',
        compute='_compute_allowed_journals',
        string='Diarios de destino permitidos',
    )

    @api.depends('company_id')
    def _compute_allowed_journals(self):
        Journal = self.env['account.journal']
        for rec in self:
            company = rec.company_id or self.env.company
            user_ou = rec._get_user_default_ou()
            if user_ou:
                from_domain = [
                    ('type', '=', 'cash'),
                    ('company_id', '=', company.id),
                    ('operating_unit_id', '=', user_ou.id),
                ]
                rec.allowed_journal_from_ids = Journal.search(from_domain)
            else:
                rec.allowed_journal_from_ids = Journal.browse()
            rec.allowed_journal_to_ids = rec._get_central_cash_journal(company)

    # ------------------------------------------------------------------
    #  Helpers Operating Unit
    # ------------------------------------------------------------------

    @api.model
    def _get_user_default_ou(self):
        user = self.env.user
        return getattr(user, 'default_operating_unit_id', False)

    @api.model
    def _find_cash_journal_by_ou(self, company, ou):
        Journal = self.env['account.journal']
        domain = [
            ('type', '=', 'cash'),
            ('company_id', '=', company.id),
        ]
        if ou:
            domain.append(('operating_unit_id', '=', ou.id))
        return Journal.search(domain, limit=1, order='id asc')

    @api.model
    def _get_central_cash_journal(self, company):
        Journal = self.env['account.journal']
        ICP = self.env['ir.config_parameter'].sudo()
        central_id = int(
            ICP.get_param('cash_transfer.central_journal_id', default='0') or 0
        )
        if central_id:
            j = Journal.browse(central_id).exists()
            if j and j.company_id == company and j.type == 'cash':
                return j
        OU = self.env['operating.unit']
        central_ou = OU.search([
            ('company_id', '=', company.id),
            '|',
            ('name', 'ilike', 'Central'),
            ('code', '=', 'CENTRAL'),
        ], limit=1)
        if central_ou:
            j = Journal.search([
                ('type', '=', 'cash'),
                ('company_id', '=', company.id),
                ('operating_unit_id', '=', central_ou.id),
            ], limit=1)
            if j:
                return j
        return Journal.browse(False)

    # ------------------------------------------------------------------
    #  Defaults y onchange
    # ------------------------------------------------------------------

    @api.model
    def default_get(self, fields_list):
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
        for rec in self:
            if not rec.company_id:
                continue
            user_ou = rec._get_user_default_ou()
            rec.journal_id_from = rec._find_cash_journal_by_ou(
                rec.company_id, user_ou,
            )
            rec.journal_id_to = rec._get_central_cash_journal(rec.company_id)

    # ------------------------------------------------------------------
    #  Cuenta principal del diario
    # ------------------------------------------------------------------

    def _main_account(self, journal):
        if not journal:
            return False
        return (
            journal.default_account_id
            or journal.payment_debit_account_id
            or journal.payment_credit_account_id
        )

    # ------------------------------------------------------------------
    #  Validar
    # ------------------------------------------------------------------

    def action_validate(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_('Solo se pueden validar transferencias en borrador.'))
            if rec.amount <= 0:
                raise UserError(_('El importe debe ser mayor a cero.'))
            if not rec.journal_id_from or not rec.journal_id_to:
                raise UserError(_('Debe seleccionar ambos diarios.'))
            if rec.journal_id_from == rec.journal_id_to:
                raise UserError(_(
                    'El diario de origen y destino no pueden ser el mismo.'
                ))

            central = rec._get_central_cash_journal(rec.company_id)
            if central and rec.journal_id_to != central:
                raise UserError(_(
                    'El diario destino debe ser el Diario Central (%s).',
                    central.name,
                ))

            acc_from = rec._main_account(rec.journal_id_from)
            acc_to = rec._main_account(rec.journal_id_to)
            if not acc_from or not acc_to:
                raise UserError(_(
                    'Falta configurar la cuenta principal en uno de los diarios.'
                ))

            company_currency = rec.company_id.currency_id
            transfer_currency = rec.currency_id
            is_foreign = transfer_currency != company_currency

            if is_foreign:
                amount_company = transfer_currency._convert(
                    rec.amount,
                    company_currency,
                    rec.company_id,
                    rec.date,
                )
            else:
                amount_company = rec.amount

            line_in = {
                'name': _('Entrada a %s', rec.journal_id_to.name),
                'account_id': acc_to.id,
                'debit': amount_company,
                'credit': 0.0,
                'company_id': rec.company_id.id,
            }
            line_out = {
                'name': _('Salida de %s', rec.journal_id_from.name),
                'account_id': acc_from.id,
                'debit': 0.0,
                'credit': amount_company,
                'company_id': rec.company_id.id,
            }

            if is_foreign:
                line_in.update({
                    'currency_id': transfer_currency.id,
                    'amount_currency': rec.amount,
                })
                line_out.update({
                    'currency_id': transfer_currency.id,
                    'amount_currency': -rec.amount,
                })

            move_vals = {
                'date': rec.date,
                'journal_id': rec.journal_id_from.id,
                'ref': _('Transferencia de efectivo #%s', rec.id),
                'line_ids': [(0, 0, line_in), (0, 0, line_out)],
                'company_id': rec.company_id.id,
            }
            move = self.env['account.move'].create(move_vals)
            move.action_post()
            rec.write({
                'state': 'validated',
                'move_id': move.id,
            })
        return True

    # ------------------------------------------------------------------
    #  Cancelar / Volver a borrador
    # ------------------------------------------------------------------

    def action_cancel(self):
        for rec in self:
            if rec.state != 'validated':
                raise UserError(_(
                    'Solo se pueden cancelar transferencias validadas.'
                ))
            if rec.move_id:
                rec.move_id.button_draft()
                rec.move_id.button_cancel()
            rec.state = 'cancelled'
        return True

    def action_view_move(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'res_id': self.move_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_draft(self):
        for rec in self:
            if rec.state != 'cancelled':
                raise UserError(_(
                    'Solo se puede volver a borrador desde estado cancelado.'
                ))
            rec.state = 'draft'
        return True
