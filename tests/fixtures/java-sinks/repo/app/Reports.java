package app;

class Reports {
    @Gov(level = "protected", reason = "export boundary", owner = "security-reviewer", sink = true)
    void download() {}

    @Gov(level = "protected", reason = "direct review only", owner = "security-reviewer")
    void directOnly() {}

    void registryTarget() {}
}
