package app;

class AccountService {
  @Transactional
  public void transfer(String from, String to) {
    audit("base");
  }

  public void status() {
  }
}
