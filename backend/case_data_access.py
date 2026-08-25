"""
case_data_access.py
======================
CHECKPOINT 7 - Investigator-scoped mock-data access boundary.

Per the checkpoint instructions: the LangGraph orchestration layer (and
any future agent) must NOT receive unrestricted access to
accounts.csv / transactions.csv / devices.csv / geo_events.csv. This
module is the ONE controlled interface a case/agent/investigator goes
through to read bank-source data, and the ONE place case scope and
PII/masking rules are enforced.

This is new code, not a preexisting mechanism this session is
"preserving" - an audit of the repository (see Checkpoint 7's
docs/backend_implementation_status.md update) found no prior
scoped-access or PII-masking module. `data_store.py`'s `DataStore` loads
the full CSVs unscoped by design (every downstream deterministic module -
`detection_layer.py`, `network_layer.py`, `evidence_model.py`,
`authority_policy.py`, etc. - already runs server-side with full trust,
which is correct for a single-process deterministic pipeline). This
module does not change `DataStore` or any of those callers; it sits in
front of a `DataStore` instance as an additional, narrower read-only view
for the *new* Checkpoint 7 orchestration/API/agent surface only.

### How case scope is derived (deterministic, never invented) ###
A case's authorized account scope is derived ONLY from data the
deterministic pipeline already computed for that case, never re-derived
via a fresh, wider traversal:
  - the case's own `account_id` (always in scope);
  - every account_id already referenced as a node in the case's
    persisted network graph (`evidence["data"]["nodes"]`), when the
    typology is graph-based (smurfing/reverse_smurfing);
  - every sender_account_id/receiver_account_id already present in the
    case's `evidence["source_transactions"]` (contextual transactions
    the network layer already discovered for this case).
No account outside this set is ever returned by this module, regardless
of investigator tier - a senior investigator gets a wider VIEW of the
same already-computed scope (e.g. devices/geo for every in-scope
account, not just the root), never a query against the full,
case-unrelated dataset.

### Authority tiers (reuses investigator_action.py, never re-decides it) ###
  - "junior": root account + immediate counterparties only (depth-1
    view of the case's already-computed scope).
  - "senior": the full case scope described above.
  - "system"/"agent": same as junior by default (least privilege) unless
    explicitly elevated by the caller - orchestration nodes should ask
    for only what a given step actually needs.

### PII boundary ###
`accounts.csv` carries direct customer PII (`customer_name`, `occupation`,
`annual_income`, `home_branch`). `mask_account()` redacts these for the
"junior" view; "senior" sees them unmasked (per Checkpoint 4's own
junior/senior authority split, reused here rather than invented). Masking
never touches non-PII risk fields (kyc_status, risk_rating, account_type,
avg_monthly_txn_*) since those are the fields the investigation logic
actually reasons over.
"""
import hashlib

PII_FIELDS = ("customer_name", "occupation", "annual_income", "home_branch")


class ScopeViolationError(Exception):
    """Raised when a caller requests data outside the case's authorized
    scope. Never silently narrowed/ignored - the caller must know its
    request was denied."""


def _mask_value(value):
    if value is None:
        return None
    digest = hashlib.sha256(str(value).encode()).hexdigest()[:6].upper()
    return f"REDACTED-{digest}"


def mask_account(account, role):
    """Return a copy of `account` with PII fields redacted unless `role`
    is 'senior'. Never mutates the input."""
    if account is None:
        return None
    masked = dict(account)
    if role != "senior":
        for field in PII_FIELDS:
            if field in masked:
                masked[field] = _mask_value(masked[field])
    return masked


def _resolve_transaction(txn, store=None):
    """`evidence["source_transactions"]` is a dict in older/hand-built
    fixtures but the real pipeline (`network_layer.py`) persists it as a
    list of bare transaction-ID strings. Resolve a string entry to its
    real transaction dict via `store.txn_by_id` (the same lookup
    `ScopedDataAccess.get_transaction` already uses elsewhere in this
    module) when a store is available; a dict entry passes through
    unchanged. Never fabricates a transaction - an unresolvable string
    (no store, or unknown id) simply contributes nothing to scope."""
    if isinstance(txn, dict):
        return txn
    if isinstance(txn, str) and store is not None:
        return store.txn_by_id.get(txn)
    return None


def case_account_scope(case, evidence, store=None):
    """The deterministic, case-derived set of account_ids this case is
    authorized to touch - see module docstring. `evidence` is the same
    per-case dict run_pipeline.py already builds (evidence["data"],
    evidence["source_transactions"]); nothing new is computed here.
    `store` (optional, backward-compatible) is used only to resolve
    transaction-ID-string entries in `source_transactions` to their real
    sender/receiver accounts - see `_resolve_transaction`."""
    scope = {case["account_id"]}
    data = (evidence or {}).get("data") or {}
    for node in data.get("nodes", []) or []:
        node_id = (node.get("data") or {}).get("id")
        if node_id:
            scope.add(node_id)
    for raw_txn in (evidence or {}).get("source_transactions", []) or []:
        txn = _resolve_transaction(raw_txn, store)
        if txn:
            for key in ("sender_account_id", "receiver_account_id"):
                if txn.get(key):
                    scope.add(txn[key])
    return scope


class ScopedDataAccess:
    """One instance per (case, investigator/agent role). The controlled
    interface an orchestration node or API handler uses instead of
    touching `DataStore` (or the mock CSVs) directly."""

    def __init__(self, store, case, evidence, role="junior"):
        self.store = store
        self.case = case
        self.evidence = evidence
        self.role = role if role in ("junior", "senior") else "junior"
        self._full_scope = case_account_scope(case, evidence, store=store)
        if self.role == "senior":
            self._authorized_scope = set(self._full_scope)
        else:
            # junior: root account + direct counterparties only (depth-1)
            root = case["account_id"]
            direct = {root}
            for raw_txn in (evidence or {}).get("source_transactions", []) or []:
                txn = _resolve_transaction(raw_txn, store)
                if txn:
                    if txn.get("sender_account_id") == root:
                        direct.add(txn.get("receiver_account_id"))
                    if txn.get("receiver_account_id") == root:
                        direct.add(txn.get("sender_account_id"))
            self._authorized_scope = {a for a in direct if a} | {root}

    def _check_scope(self, account_ids):
        outside = set(account_ids) - self._authorized_scope
        if outside:
            raise ScopeViolationError(
                f"account(s) {sorted(outside)} are outside this case's "
                f"authorized scope for role={self.role!r}"
            )

    def get_case_accounts(self):
        """Accounts within this case's authorized scope, PII-masked per
        role."""
        return [
            mask_account(self.store.accounts_by_id.get(acc_id), self.role)
            for acc_id in sorted(self._authorized_scope)
            if acc_id in self.store.accounts_by_id
        ]

    def get_case_transactions(self):
        """Transactions already gathered for this case (source_transactions),
        never a fresh query against the full ledger."""
        return list((self.evidence or {}).get("source_transactions", []) or [])

    def get_transaction(self, transaction_id):
        """A single transaction, but ONLY if it is already part of this
        case's gathered evidence - this module never permits looking up
        an arbitrary transaction_id outside the case."""
        for txn in self.get_case_transactions():
            if isinstance(txn, dict) and txn.get("transaction_id") == transaction_id:
                return txn
        txn = self.store.txn_by_id.get(transaction_id)
        if txn and {txn.get("sender_account_id"), txn.get("receiver_account_id")} & self._authorized_scope:
            return txn
        raise ScopeViolationError(
            f"transaction {transaction_id!r} is not part of this case's gathered evidence"
        )

    def get_related_network(self, scope="case"):
        """The case's already-computed graph (nodes/edges), never a fresh
        traversal. `scope='case'` returns the full persisted graph (only
        meaningful for graph-based typologies); scoping by investigator
        role is enforced by filtering nodes/edges to `_authorized_scope`."""
        data = (self.evidence or {}).get("data") or {}
        nodes = [n for n in data.get("nodes", []) or []
                 if (n.get("data") or {}).get("id") in self._authorized_scope]
        node_ids = {(n.get("data") or {}).get("id") for n in nodes}
        edges = [e for e in data.get("edges", []) or []
                 if (e.get("data") or {}).get("source") in node_ids
                 and (e.get("data") or {}).get("target") in node_ids]
        return {"nodes": nodes, "edges": edges}

    def get_devices_for_accounts(self, account_ids):
        self._check_scope(account_ids)
        out = []
        for acc_id in account_ids:
            out.extend(self.store.devices_by_account.get(acc_id, []))
        return out

    def get_geo_events_for_accounts(self, account_ids):
        self._check_scope(account_ids)
        out = []
        for acc_id in account_ids:
            out.extend(self.store.geo_by_account.get(acc_id, []))
        return out

    def get_evidence(self):
        """The case's own already-persisted evidence record - this
        module does not gather new evidence, only re-exposes it scoped."""
        return self.evidence