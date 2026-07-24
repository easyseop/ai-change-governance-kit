package app;

class JpqlRepository {
  @Query("select a from Account a")
  public void readOnlyQuery() {
    audit("base");
  }
}
