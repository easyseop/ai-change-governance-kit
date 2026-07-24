package app;

@Service
class AccountService {
  @Transactional
  public void transfer(String from, String to) {
    audit();
  }

  public void transfer(String account) {
  }

  class Inner {
    @Secured("ADMIN")
    Inner(int id) {
    }

    String status() {
      return "ok";
    }
  }
}
