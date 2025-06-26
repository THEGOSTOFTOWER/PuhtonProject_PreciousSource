# Telegram Bot Interface Prototype

```mermaid
graph TD
    A[Start /start] --> B[Main Menu]
    B --> C[Show Habits]
    B --> D[Create Habit]
    B --> E[Show Stats]
    B --> F[Show Charts]
    B --> G[Help]
    D --> H[Enter Name]
    H --> I[Select Frequency]
    I --> J[Enter Description]
    J --> K[Habit Created]
    C --> L[Complete Habit]
    F --> M[Select Habit Chart]
    F --> N[Overview Chart]

