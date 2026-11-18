

``` mermaid
flowchart TB
  subgraph L1["L1 · interfaces"]
    API["API · CLI/TUI · MCP<br/>协议适配 / 组合根"]
  end

  subgraph L2["L2 · apps"]
    Chat["apps/chat<br/>聊天用例与产品策略<br/>候选边界"]
    Goal["apps/goal<br/>目标/长程任务用例"]
  end

  subgraph L3["L3 · kernel"]
    subgraph Control["控制与准入"]
      Gateway["gateway<br/>统一准入 / 串行 / fence / cancel"]
      Orch["orchestration<br/>WorkRequest / EntryPoint / Mailbox<br/>通用 Ticket / Assignment"]
      Controller["SessionController<br/>驱动一次 turn/run"]
    end

    subgraph Execution["执行机制（职责不同，保留分立）"]
      Runtime["runtimes<br/>GraphEngine / 通用图执行"]
      Adapter["runtimes/nodes<br/>AgentLoop 节点适配"]
      Agent["agent<br/>AgentLoop / tools / strategy / context"]
      Workflow["workflow<br/>可持久化、审批、暂停/恢复的流程"]
      Swarm["swarm<br/>多 Agent 拓扑与协作"]
      Evolution["evolution<br/>提案 / 验证 / 回放 / 回滚"]
    end
    
    subgraph State["会话、事件与共享能力"]
      Session["session<br/>会话 / attempt / SessionStore"]
      StateStore["session/state<br/>EventStore / cache / history facade"]
      Projector["Projector / ProjectionStore<br/>messages 读模型"]
      Events["events<br/>EventV2 / EventType 协议"]
      Memory["memory<br/>跨会话长期记忆"]
      Context["context<br/>平台级上下文供给"]
      Permission["permission"]
      Skills["skills"]
      Hooks["hooks<br/>评估是否收进 agent/hooks"]
    end

  end

  subgraph L4["L4 · foundation"]
    Infra["LLM / SQLite / PlatformStore<br/>配置 / 日志 / 通用工具"]
  end

  API --> Chat
  Chat --> Gateway
  API -. "无 chat 用例策略的入口可直达对应 port" .-> Gateway
  Goal --> Swarm
  Goal --> Workflow

  Gateway --> Orch
  Gateway --> Controller
  Controller --> Runtime
  Runtime --> Adapter
  Adapter --> Agent
  Swarm --> Runtime
  Workflow --> Agent

  Gateway --> Session
  Session --> StateStore
  StateStore --> Projector
  Session --> Events
  StateStore --> Events
  Agent --> Memory
  Agent --> Context
  Agent --> Permission
  Agent --> Skills
  Agent --> Hooks

  Agent --> Infra
  StateStore --> Infra
  Workflow --> Infra
  Evolution --> Infra
```
