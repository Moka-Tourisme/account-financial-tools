# Copyright 2012-2021 Akretion (http://www.akretion.com/)
# @author: Benoît GUILLOT <benoit.guillot@akretion.com>
# @author: Chafique DELLI <chafique.delli@akretion.com>
# @author: Alexis de Lattre <alexis.delattre@akretion.com>
# @author: Mourad EL HADJ MIMOUNE <mourad.elhadj.mimoune@akretion.com>
# Copyright 2018-2021 Tecnativa - Pedro M. Baeza
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

logger = logging.getLogger(__name__)


class AccountCheckDeposit(models.Model):
    _name = "account.check.deposit"
    _description = "Account Check Deposit"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "deposit_date desc"
    _check_company_auto = True

    name = fields.Char(size=64, readonly=True, default="/", copy=False)
    check_payment_ids = fields.One2many(
        comodel_name="account.move.line",
        inverse_name="check_deposit_id",
        string="Check Payments",
        states={"done": [("readonly", "=", True)]},
    )
    deposit_date = fields.Date(
        required=True,
        states={"done": [("readonly", "=", True)]},
        default=fields.Date.context_today,
        tracking=True,
        copy=False,
    )
    journal_id = fields.Many2one(
        comodel_name="account.journal",
        string="Check Journal",
        domain="[('company_id', '=', company_id), ('type', '=', 'bank'), "
        "('bank_account_id', '=', False)]",
        required=True,
        check_company=True,
        states={"done": [("readonly", "=", True)]},
        tracking=True,
    )
    in_hand_check_account_id = fields.Many2one(
        comodel_name="account.account",
        compute="_compute_in_hand_check_account_id",
        store=True,
    )
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        required=True,
        states={"done": [("readonly", "=", True)]},
        tracking=True,
    )
    state = fields.Selection(
        selection=[("draft", "Draft"), ("done", "Done")],
        string="Status",
        default="draft",
        readonly=True,
        tracking=True,
    )
    move_id = fields.Many2one(
        comodel_name="account.move",
        string="Journal Entry",
        readonly=True,
        check_company=True,
    )
    bank_journal_id = fields.Many2one(
        comodel_name="account.journal",
        string="Bank Account",
        required=True,
        domain="[('company_id', '=', company_id), ('type', '=', 'bank'), "
        "('bank_account_id', '!=', False)]",
        check_company=True,
        states={"done": [("readonly", "=", True)]},
        tracking=True,
    )
    line_ids = fields.One2many(
        comodel_name="account.move.line",
        related="move_id.line_ids",
        string="Lines",
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        required=True,
        states={"done": [("readonly", "=", True)]},
        default=lambda self: self.env.company,
        tracking=True,
    )
    total_amount = fields.Monetary(
        compute="_compute_check_deposit",
        store=True,
        currency_field="currency_id",
        tracking=True,
    )
    check_count = fields.Integer(
        compute="_compute_check_deposit",
        store=True,
        string="Number of Checks",
        tracking=True,
    )

    _sql_constraints = [
        (
            "name_company_unique",
            "unique(company_id, name)",
            "A check deposit with this reference already exists in this company.",
        )
    ]

    @api.depends(
        "company_id",
        "currency_id",
        "check_payment_ids.debit",
        "check_payment_ids.amount_currency",
        "move_id.line_ids.reconciled",
    )
    def _compute_check_deposit(self):
        rg_res = self.env["account.move.line"].read_group(
            [("check_deposit_id", "in", self.ids)],
            ["check_deposit_id", "debit", "amount_currency"],
            ["check_deposit_id"],
        )
        logger.info(rg_res)
        mapped_data = {
            x["check_deposit_id"][0]: {
                "debit": x["debit"],
                "amount_currency": x.get("amount_currency", 0),
                "count": x["check_deposit_id_count"],
            }
            for x in rg_res
        }

        for deposit in self:
            if deposit.company_id.currency_id != deposit.currency_id:
                total = mapped_data.get(deposit.id, {"amount_currency": 0.0})[
                    "amount_currency"
                ]
            else:
                total = mapped_data.get(deposit.id, {"debit": 0.0})["debit"]
            count = mapped_data.get(deposit.id, {"count": 0})["count"]
            deposit.total_amount = total
            deposit.check_count = count

    @api.depends("journal_id")
    def _compute_in_hand_check_account_id(self):
        for rec in self:
            account = rec.journal_id.inbound_payment_method_line_ids.filtered(
                lambda line: line.payment_method_id.code == "manual"
            ).payment_account_id
            rec.in_hand_check_account_id = account

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        ajo = self.env["account.journal"]
        company_id = res.get("company_id")
        # pre-set journal_id and bank_journal_id is there is only one
        domain = [("company_id", "=", company_id), ("type", "=", "bank")]
        journals = ajo.search(domain + [("bank_account_id", "=", False)])
        if len(journals) == 1:
            res["journal_id"] = journals.id
        bank_journals = ajo.search(domain + [("bank_account_id", "!=", False)])
        if len(bank_journals) == 1:
            res["bank_journal_id"] = bank_journals.id
        return res

    @api.constrains("currency_id", "check_payment_ids", "company_id")
    def _check_deposit(self):
        for deposit in self:
            deposit_currency = deposit.currency_id
            for line in deposit.check_payment_ids:
                if line.currency_id != deposit_currency:
                    raise ValidationError(
                        _(
                            "The check with amount {amount} and reference '{ref}' "
                            "is in currency {check_currency} but the deposit is in "
                            "currency {deposit_currency}."
                        ).format(
                            amount=line.debit,
                            ref=line.ref or "",
                            check_currency=line.currency_id.name,
                            deposit_currency=deposit_currency.name,
                        )
                    )

    def unlink(self):
        for deposit in self.filtered(lambda x: x.state == "done"):
            raise UserError(
                _(
                    "The deposit '%s' is in valid state, so you must "
                    "cancel it before deleting it."
                )
                % deposit.name
            )
        return super().unlink()

    def backtodraft(self):
        for deposit in self:
            if deposit.move_id:
                move = deposit.move_id
                # It will raise here if journal_id.update_posted = False
                if move.state == "posted":
                    move.button_draft()
                for line in deposit.check_payment_ids:
                    if line.reconciled:
                        line.remove_move_reconcile()
                move.unlink()
            deposit.write({"state": "draft"})
        return True

    @api.model
    def create(self, vals):
        if "company_id" in vals:
            self = self.with_company(vals["company_id"])
        if vals.get("name", "/") == "/":
            vals["name"] = self.env["ir.sequence"].next_by_code(
                "account.check.deposit", vals.get("deposit_date")
            )
        return super().create(vals)

    def _prepare_account_move_vals(self):
        self.ensure_one()
        move_vals = {
            "journal_id": self.journal_id.id,
            "date": self.deposit_date,
            "ref": _("Check Deposit %s") % self.name,
            "company_id": self.company_id.id,
        }
        return move_vals

    @api.model
    def _prepare_move_line_vals(self, line):
        assert line.debit > 0, "Debit must have a value"
        return {
            "name": line.ref and _("Check Ref. %s") % line.ref or False,
            "credit": line.debit,
            "debit": 0.0,
            "account_id": line.account_id.id,
            "partner_id": line.partner_id.id,
            "currency_id": line.currency_id.id or False,
            "amount_currency": line.amount_currency * -1,
        }

    def _prepare_counterpart_move_lines_vals(self, total_debit, total_amount_currency):
        self.ensure_one()
        account_id = False
        for line in self.bank_journal_id.inbound_payment_method_line_ids:
            if line.payment_method_id.code == "manual" and line.payment_account_id:
                account_id = line.payment_account_id.id
                break
        if not account_id:
            account_id = self.company_id.account_journal_payment_debit_account_id.id
        if not account_id:
            raise UserError(
                _("Missing 'Outstanding Receipts Account' on the company '%s'.")
                % self.company_id.display_name
            )
        return {
            "debit": total_debit,
            "credit": 0.0,
            "account_id": account_id,
            "partner_id": False,
            "currency_id": self.currency_id.id or False,
            "amount_currency": total_amount_currency,
        }

    def validate_deposit(self):
        am_obj = self.env["account.move"]
        move_line_obj = self.env["account.move.line"]
        for deposit in self:
            move = am_obj.create(deposit._prepare_account_move_vals())
            total_debit = 0.0
            total_amount_currency = 0.0
            to_reconcile_lines = []
            for line in deposit.check_payment_ids:
                total_debit += line.debit
                total_amount_currency += line.amount_currency
                line_vals = self._prepare_move_line_vals(line)
                line_vals["move_id"] = move.id
                move_line = move_line_obj.with_context(
                    check_move_validity=False
                ).create(line_vals)
                to_reconcile_lines.append(line + move_line)

            # Create counter-part
            counter_vals = deposit._prepare_counterpart_move_lines_vals(
                total_debit, total_amount_currency
            )
            counter_vals["move_id"] = move.id
            move_line_obj.create(counter_vals)
            move.action_post()

            deposit.write({"state": "done", "move_id": move.id})
            for reconcile_lines in to_reconcile_lines:
                reconcile_lines.reconcile()
        return True

    @api.onchange("company_id")
    def onchange_company_id(self):
        if self.company_id:
            self.bank_journal_id = self.env["account.journal"].search(
                [
                    ("company_id", "=", self.company_id.id),
                    ("type", "=", "bank"),
                    ("bank_account_id", "!=", False),
                ],
                limit=1,
            )
        else:
            self.bank_journal_id = False

    @api.onchange("journal_id")
    def onchange_journal_id(self):
        self.currency_id = (
            self.journal_id.currency_id or self.journal_id.company_id.currency_id
        )

    def get_report(self):
        report = self.env.ref("account_check_deposit.report_account_check_deposit")
        action = report.report_action(self)
        return action

    def get_all_checks(self):
        self.ensure_one()
        all_pending_checks = self.env["account.move.line"].search(
            [
                ("company_id", "=", self.company_id.id),
                ("reconciled", "=", False),
                ("account_id", "=", self.in_hand_check_account_id.id),
                ("debit", ">", 0),
                ("check_deposit_id", "=", False),
                ("currency_id", "=", self.currency_id.id),
                ("parent_state", "=", "posted"),
            ]
        )
        if all_pending_checks:
            self.message_post(body=_("Get All Received Checks"))
            all_pending_checks.write({"check_deposit_id": self.id})
        else:
            raise UserError(
                _(
                    "There are no received checks in account '{account}' in currency "
                    "'{currency}' that are not already in this check deposit."
                ).format(
                    account=self.in_hand_check_account_id.display_name,
                    currency=self.currency_id.display_name,
                )
            )
