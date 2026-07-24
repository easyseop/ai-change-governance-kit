interface Task { void exec(); }
class Vault { void transfer() { int value = 2; } }
class Flow {
    Task task = () -> new Vault().transfer();
    Object registry;
    void sink() { registry.find("pay").exec(); }
}
