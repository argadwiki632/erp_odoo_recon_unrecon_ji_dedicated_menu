from ast import literal_eval

from odoo import Command
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests import tagged


@tagged("post_install", "-at_install")
class TestJournalItemsReconciliation(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.receivable = cls.company_data["default_account_receivable"]
        cls.counterpart = cls.company_data["default_account_revenue"]
        cls.counterpart.reconcile = False

    def _line(self, balance, account=None, posted=True):
        account = account or self.receivable
        move = self.env["account.move"].create({
            "journal_id": self.company_data["default_journal_misc"].id,
            "date": "2026-01-15",
            "line_ids": [
                Command.create({
                    "account_id": account.id, "partner_id": self.partner_a.id,
                    "name": "Reconciliation test", "balance": balance,
                }),
                Command.create({
                    "account_id": self.counterpart.id, "partner_id": self.partner_a.id,
                    "name": "Counterpart", "balance": -balance,
                }),
            ],
        })
        if posted:
            move.action_post()
        return move.line_ids.filtered(lambda line: line.account_id == account)

    def _items(self, reconciled, **context):
        name = "reconciled" if reconciled else "unreconciled"
        action = self.env.ref(f"arga_journal_items_reconciliation.{name}_journal_items_action")
        return self.env["account.move.line"].with_context(**context).search(literal_eval(action.domain))

    def test_partial_full_and_unreconcile(self):
        invoice = self._line(100)
        payment = self._line(-40)
        (invoice | payment).reconcile()
        self.assertEqual(invoice.amount_residual, 60)
        self.assertEqual(payment.amount_residual, 0)
        self.assertTrue(payment.reconciled)
        self.assertFalse((invoice | payment).full_reconcile_id)
        for line in invoice | payment:
            self.assertIn(line, self._items(False))
            self.assertNotIn(line, self._items(True))
        final_payment = self._line(-60)
        (invoice | final_payment).reconcile()
        lines = invoice | payment | final_payment
        self.assertTrue(lines.full_reconcile_id)
        for line in lines:
            self.assertIn(line, self._items(True))
            self.assertNotIn(line, self._items(False))
        lines.remove_move_reconcile()
        for line in lines:
            self.assertIn(line, self._items(False))
            self.assertNotIn(line, self._items(True))

    def test_other_accounts_and_draft_filter(self):
        account = self.env["account.account"].create({
            "name": "Reconcilable Clearing", "code": "RECMENU",
            "account_type": "asset_current", "reconcile": True,
        })
        debit = self._line(25, account)
        credit = self._line(-25, account)
        self.assertIn(debit, self._items(False))
        (debit | credit).reconcile()
        self.assertIn(debit, self._items(True))
        self.assertIn(credit, self._items(True))
        for line in (debit | credit).move_id.line_ids.filtered(lambda line: line.account_id == self.counterpart):
            self.assertIn(line, self._items(False))
            self.assertNotIn(line, self._items(True))
        draft = self._line(10, posted=False)
        self.assertIn(draft, self._items(False))
        action = self.env.ref("arga_journal_items_reconciliation.unreconciled_journal_items_action")
        posted_domain = literal_eval(action.domain) + [("parent_state", "=", "posted")]
        self.assertNotIn(draft, self.env["account.move.line"].search(posted_domain))
        self.assertEqual(literal_eval(action.context)["search_default_posted"], 1)

    def test_payable_and_company_rules(self):
        payable = self.company_data["default_account_payable"]
        debit = self._line(80, payable)
        credit = self._line(-80, payable)
        self.assertIn(debit, self._items(False))
        (debit | credit).reconcile()
        self.assertIn(debit, self._items(True))
        other_company = self.env["res.company"].create({"name": "Other Reconciliation Company"})
        reader = self.env["res.users"].create({
            "name": "Reconciliation Reader", "login": "reconciliation_reader",
            "company_id": self.env.company.id,
            "company_ids": [Command.set([self.env.company.id, other_company.id])],
            "groups_id": [Command.set([self.env.ref("account.group_account_readonly").id])],
        })
        action = self.env.ref("arga_journal_items_reconciliation.reconciled_journal_items_action")
        model = self.env["account.move.line"].with_user(reader)
        self.assertIn(debit, model.search(literal_eval(action.domain)))
        self.assertNotIn(debit, model.with_context(
            allowed_company_ids=[other_company.id],
        ).search(literal_eval(action.domain)))

    def test_menu_and_native_views(self):
        journal_items = self.env.ref("account.menu_action_account_moves_all")
        reconciled = self.env.ref("arga_journal_items_reconciliation.reconciled_journal_items_menu")
        unreconciled = self.env.ref("arga_journal_items_reconciliation.unreconciled_journal_items_menu")
        self.assertLess(journal_items.sequence, reconciled.sequence)
        self.assertLess(reconciled.sequence, unreconciled.sequence)
        for prefix in ("reconciled", "unreconciled"):
            menu = self.env.ref(f"arga_journal_items_reconciliation.{prefix}_journal_items_menu")
            self.assertEqual(menu.parent_id, journal_items.parent_id)
            self.assertEqual(menu.groups_id, self.env.ref("account.group_account_readonly"))
            self.assertEqual(menu.action.res_model, "account.move.line")
            self.assertEqual(menu.action.view_mode, "tree,pivot,graph,kanban")
            self.assertEqual(menu.action.view_id, self.env.ref("account.view_move_line_tree"))
            self.assertEqual(menu.action.search_view_id, self.env.ref("account.view_account_move_line_filter"))
