package app;

class AccountRepository {
  @Query(nativeQuery = true)
  public void rewriteBalance() {
    audit("head");
  }
}
