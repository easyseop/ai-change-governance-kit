package app;

class AuthController {
  @PreAuthorize("hasRole('ADMIN')")
  public void resetPassword() {
    audit("head");
  }
}
