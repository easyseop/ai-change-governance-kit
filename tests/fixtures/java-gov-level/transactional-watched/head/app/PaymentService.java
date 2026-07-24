package app;

class PaymentService {
  @Transactional
  public void refreshCache() {
    audit("head");
  }
}
