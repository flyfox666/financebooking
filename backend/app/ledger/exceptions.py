class LedgerError(Exception):
    pass


class BookError(LedgerError):
    pass


class AccountError(LedgerError):
    pass


class VoucherError(LedgerError):
    pass
