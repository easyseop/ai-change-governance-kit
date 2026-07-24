package app;

class InvalidGov {
  @Gov(level = DYNAMIC_LEVEL)
  public void guarded() {
    audit("base");
  }
}
