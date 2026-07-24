package app;

class Guard {
  @org.springframework.security.access.prepost.PreAuthorize("hasRole('ADMIN')")
  public void enforce() {
    audit("base");
  }
}
