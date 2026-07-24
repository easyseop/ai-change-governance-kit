interface Task { void exec(); }
class Vault { void transfer() { int value = 1; } }
class Registry {
    void put(String key, Task task) { int marker = 1; }
    Task find(String key) { return null; }
}
class Flow {
    Registry reg = new Registry();
    Vault vault = new Vault();
    void wire() { reg.put("pay", vault::transfer); }
    void mid(String key) { reg.find(key).exec(); }
    void sink(String key) { mid(key); }
}
