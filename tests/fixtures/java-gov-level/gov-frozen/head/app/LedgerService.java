package app;

class LedgerService {
  @Gov(level = "frozen", reason = "ledger write")
  public void writeEntry() {
    audit("head");
  }
}
