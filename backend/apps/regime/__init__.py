"""行情阶段机制：日频判定、切片、Shadow 记录（零执行）。

第①段（判定 + 切片 + Shadow 记录）的落点 app。分期见 CONTEXT.md：
① 判定/切片/Shadow（本 app）② 事件熔断 ③ 行情阶段 gate。
"""
