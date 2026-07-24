package app;

class AccountService {
  @Audited
  @Transactional
  public void transfer(String from, String to) {
    audit("head");
  }

  public void status() {
  }
}
