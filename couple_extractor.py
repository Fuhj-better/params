"""
完整的参数耦合分析系统
基于已构建的代码依赖关系 (dependency_analysis.json)
作者: @Fuhj-better
日期: 2025-11-11
"""

import json
import os
import re
from pathlib import Path
from typing import List, Dict, Any, Set, Tuple
from collections import defaultdict
from dataclasses import dataclass, asdict
import networkx as nx


# =============================================================================
# 数据类定义
# =============================================================================

@dataclass
class ParamInfo:
    """参数配置信息"""
    name: str
    type: str
    default: str
    range: str
    comment: str

@dataclass
class FileMatchResult:
    """文件匹配结果"""
    file_path: str
    matched_params: List[str]
    params_info: List[Dict]
    match_count: int
    contexts: List[Dict]


# =============================================================================
# Step 1: 加载依赖信息和参数配置
# =============================================================================

class DependencyLoader:
    """加载代码依赖信息和参数配置"""
    
    def __init__(self, dependency_json: Path, clusters_json: Path, params_file: Path):
        self.dependency_json = dependency_json
        self.clusters_json = clusters_json
        self.params_file = params_file
        
        self.dependency_data = None
        self.clusters = None
        self.params_info = {}  # 存储完整的参数信息

    def parse_param_line(self, line: str) -> ParamInfo:
        """
        解析参数配置行
        格式: PARAM_NAME(type) value (range) [# comment]
        例如: FDAE_WIDTH(int) 32 (1-1024) # Data width
        """
        line = line.strip()
        if not line or line.startswith('#'):
            return None
        
        # 提取注释
        comment = ''
        if '#' in line:
            line, comment = line.split('#', 1)
            comment = comment.strip()
            line = line.strip()
        
        # 匹配格式: NAME(type) value (range)
        pattern = r'(\S+)\s*\((\w+)\)\s+(\S+)(?:\s+\(([^)]+)\))?'
        match = re.match(pattern, line)
        
        if not match:
            return None
        
        return ParamInfo(
            name=match.group(1),
            type=match.group(2),
            default=match.group(3),
            range=match.group(4) if match.group(4) else '',
            comment=comment
        )
    
    def load(self):
        """加载所有必要数据"""
        
        print("="*70)
        print("📂 Step 1: 加载数据")
        print("="*70)
        
        # 1. 加载依赖分析结果
        if self.dependency_json.exists():
            with open(self.dependency_json, 'r', encoding='utf-8') as f:
                self.dependency_data = json.load(f)
            
            summary = self.dependency_data.get('dependency_analysis', {}).get('summary', {})
            print(f"✅ 加载代码依赖信息:")
            print(f"   - 文件数: {summary.get('total_files', 0)}")
            print(f"   - 依赖关系: {summary.get('total_dependencies', 0)}")
            print(f"   - 模块实例化: {summary.get('module_dependencies', 0)}")
        else:
            print(f"⚠️  未找到 {self.dependency_json}")
            self.dependency_data = {}
        
        # 2. 加载参数簇
        if self.clusters_json.exists():
            with open(self.clusters_json, 'r', encoding='utf-8') as f:
                self.clusters = json.load(f)
            print(f"✅ 加载参数簇: {len(self.clusters)} 个簇")
        else:
            print(f"⚠️  未找到 {self.clusters_json}")
            self.clusters = {}
        
        # 3. 加载并解析参数配置文件
        if self.params_file.exists():
            with open(self.params_file, 'r', encoding='utf-8') as f:
                for line in f:
                    param_info = self.parse_param_line(line)
                    if param_info:
                        self.params_info[param_info.name] = param_info
            
            print(f"✅ 加载参数配置: {len(self.params_info)} 个参数")
            
            # 打印示例
            if self.params_info:
                first_param = next(iter(self.params_info.values()))
                print(f"   示例参数: {first_param.name}")
                print(f"     - 类型: {first_param.type}")
                print(f"     - 默认值: {first_param.default}")
                print(f"     - 范围: {first_param.range}")
                if first_param.comment:
                    print(f"     - 说明: {first_param.comment}")
        else:
            print(f"⚠️  未找到配置文件 {self.params_file}")
        
        print()
        return self


# =============================================================================
# Step 2: 字符串匹配 - 找出哪些文件使用了哪些参数
# =============================================================================

class StringMatcher:
    """字符串匹配：按簇扫描文件中的参数"""
    
    def __init__(self, driver_root: Path, clusters: Dict, params_info: Dict[str, ParamInfo]):
        self.driver_root = driver_root
        self.clusters = clusters
        self.params_info = params_info

    def remove_noise(self, content: str) -> str:
        """移除注释和字符串字面量（减少误匹配）"""
        # 移除单行注释 //
        content = re.sub(r'//.*?$', '', content, flags=re.MULTILINE)
        # 移除多行注释 /* */
        content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
        # 移除字符串 "..."
        content = re.sub(r'"[^"]*"', '""', content)
        return content
    
    def extract_context(self, content: str, match_pos: int, window: int = 200) -> Dict:
        """提取匹配位置的代码上下文"""
        lines = content[:match_pos].count('\n')
        start = max(0, match_pos - window)
        end = min(len(content), match_pos + window)
        
        return {
            'line_number': lines + 1,
            'snippet': content[start:end],
            'position': match_pos
        }
    
    def match_params_in_file(self, file_path: Path, cluster_params: List[str]) -> FileMatchResult:
        """在单个文件中匹配指定簇的参数"""
        
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                raw_content = f.read()
        except Exception as e:
            return None
        
        # 清理内容
        cleaned_content = self.remove_noise(raw_content)
        
        matched_params = []
        contexts = []
        
        # 只扫描该簇的参数
        for param_name in cluster_params:
            if param_name not in self.params_info:
                continue
            
            # 使用词边界正则（避免部分匹配）
            pattern = r'\b' + re.escape(param_name) + r'\b'
            matches = list(re.finditer(pattern, cleaned_content))
            
            if matches:
                matched_params.append(param_name)
                
                # 保存前3个匹配位置的上下文
                for match in matches[:3]:
                    ctx = self.extract_context(cleaned_content, match.start())
                    ctx['param'] = param_name
                    contexts.append(ctx)
        
        if not matched_params:
            return None
        
        # 构建参数详细信息
        params_with_info = []
        for param in matched_params:
            param_config = self.params_info[param]
            params_with_info.append({
                'name': param_config.name,
                'type': param_config.type,
                'default': param_config.default,
                'range': param_config.range,
                'comment': param_config.comment
            })
        
        return FileMatchResult(
            file_path=str(file_path),
            matched_params=sorted(matched_params),
            params_info=params_with_info,
            match_count=len(matched_params),
            contexts=contexts
        )
    
    def scan_cluster(self, cluster_name: str, cluster_params: List[str]) -> List[Dict]:
        """扫描一个参数簇"""
        
        results = []
        
        # 获取所有文件
        all_files = [f for f in self.driver_root.rglob('*') if f.is_file()]
        print(f"共 {len(all_files)} 个文件")
        
        for file_path in all_files:
            result = self.match_params_in_file(file_path, cluster_params)
            if result:
                results.append({
                    'file': result.file_path,
                    'matched_params': result.matched_params,
                    'params_info': result.params_info,
                    'match_count': result.match_count,
                    'contexts': result.contexts
                })
        
        # 按匹配数量排序
        results.sort(key=lambda x: x['match_count'], reverse=True)
        
        return results
    
    def scan_all(self) -> Dict[str, List[Dict]]:
        """按簇扫描所有文件"""
        
        print("="*70)
        print("🔍 Step 2: 字符串匹配扫描（按簇）")
        print("="*70)
        
        all_files = [f for f in self.driver_root.rglob('*') if f.is_file()]
        print(f"共 {len(all_files)} 个文件")
        print(f"共 {len(self.clusters)} 个参数簇\n")
        
        candidates = {}
        
        # 为每个簇独立扫描
        for cluster_name, cluster_params in self.clusters.items():
            print(f"📦 扫描簇: {cluster_name} ({len(cluster_params)} 个参数)...", end=' ')
            
            cluster_results = self.scan_cluster(cluster_name, cluster_params)
            candidates[cluster_name] = cluster_results
            
            print(f"找到 {len(cluster_results)} 个文件")
        
        # 统计
        total_matches = sum(len(files) for files in candidates.values())
        print(f"\n✅ 总计: {total_matches} 个文件-簇匹配")
        print(f"✅ 已附加完整参数配置信息\n")
        
        return candidates


# =============================================================================
# Step 3: 基于依赖关系构建文件对
# =============================================================================

class FilePairBuilder:
    """基于代码依赖关系构建文件对"""
    
    def __init__(self, dependency_data: Dict, candidates: Dict):
        self.dependency_data = dependency_data
        self.candidates = candidates
        
    def build_pairs(self) -> List[Dict]:
        """从 dependency_analysis.json 提取文件对，支持单端多簇和双端分析"""
        
        print("="*70)
        print("🔗 Step 3: 构建文件调用对（基于依赖分析）")
        print("="*70)
        
        module_deps = (self.dependency_data
                      .get('dependency_analysis', {})
                      .get('dependency_relationships', {})
                      .get('module_dependencies', []))
        
        file_pairs = []
        
        # 构建文件->参数映射（包含完整信息）
        file_to_params = {}
        for cluster_name, files in self.candidates.items():
            for f in files:
                fp = f['file']
                if fp not in file_to_params:
                    file_to_params[fp] = {
                        'params': set(),
                        'params_info': [],
                        'clusters': set(),
                        'contexts': []
                    }
                file_to_params[fp]['params'].update(f['matched_params'])
                file_to_params[fp]['params_info'].extend(f['params_info'])
                file_to_params[fp]['clusters'].add(cluster_name)
                file_to_params[fp]['contexts'].extend(f['contexts'])
        
        # 构建文件对
        for dep in module_deps:
            caller = dep.get('source_path')
            callee = dep.get('target_path')
            
            caller_info = file_to_params.get(caller)
            callee_info = file_to_params.get(callee)
            
            # 情况1: 双端都有参数 - 分析跨文件耦合
            if caller_info and callee_info:
                file_pairs.append({
                    'type': 'INTER_FILE',  # 跨文件分析
                    'caller_file': caller,
                    'callee_file': callee,
                    'module': dep.get('module_type'),
                    'instance': dep.get('instance_name'),
                    'instantiation_code': dep.get('description', ''),
                    'caller_params': sorted(list(caller_info['params'])),
                    'caller_params_info': caller_info['params_info'],
                    'callee_params': sorted(list(callee_info['params'])),
                    'callee_params_info': callee_info['params_info'],
                    'caller_clusters': sorted(list(caller_info['clusters'])),
                    'callee_clusters': sorted(list(callee_info['clusters']))
                })
            
            # 情况2: 只有caller有参数，且属于多个簇 - 分析簇间耦合
            elif caller_info and not callee_info and len(caller_info['clusters']) > 1:
                file_pairs.append({
                    'type': 'INTRA_FILE_MULTI_CLUSTER',  # 单文件多簇分析
                    'file': caller,
                    'role': 'caller',
                    'module': dep.get('module_type'),
                    'instance': dep.get('instance_name'),
                    'instantiation_code': dep.get('description', ''),
                    'params': sorted(list(caller_info['params'])),
                    'params_info': caller_info['params_info'],
                    'clusters': sorted(list(caller_info['clusters'])),
                    'other_file': callee  # 记录关联文件
                })
            
            # 情况3: 只有callee有参数，且属于多个簇 - 分析簇间耦合
            elif callee_info and not caller_info and len(callee_info['clusters']) > 1:
                file_pairs.append({
                    'type': 'INTRA_FILE_MULTI_CLUSTER',  # 单文件多簇分析
                    'file': callee,
                    'role': 'callee',
                    'module': dep.get('module_type'),
                    'instance': dep.get('instance_name'),
                    'instantiation_code': dep.get('description', ''),
                    'params': sorted(list(callee_info['params'])),
                    'params_info': callee_info['params_info'],
                    'clusters': sorted(list(callee_info['clusters'])),
                    'other_file': caller  # 记录关联文件
                })
        
        # 统计
        inter_file_count = sum(1 for p in file_pairs if p['type'] == 'INTER_FILE')
        intra_file_count = sum(1 for p in file_pairs if p['type'] == 'INTRA_FILE_MULTI_CLUSTER')
        
        print(f"✅ 构建了 {len(file_pairs)} 个分析任务:")
        print(f"   - 跨文件分析 (两端都有参数): {inter_file_count}")
        print(f"   - 单文件多簇分析 (一端多簇): {intra_file_count}")
        print(f"✅ 每个任务都包含完整的参数配置信息\n")
        
        return file_pairs


# =============================================================================
# Step 4: LLM 分析文件对
# =============================================================================

class LLMCouplingAnalyzer:
    """使用LLM分析文件对的参数耦合"""
    
    def __init__(self, file_pairs: List[Dict], clusters_def: Dict):
        self.file_pairs = file_pairs
        self.clusters_def = clusters_def  # 保存原始簇定义，用于分组参数
    
    def format_params_info(self, params_info: List[Dict]) -> str:
        """格式化参数信息为易读文本"""
        lines = []
        for p in params_info:
            line = f"  - {p['name']} ({p['type']})"
            if p['default']:
                line += f" = {p['default']}"
            if p['range']:
                line += f" [{p['range']}]"
            if p['comment']:
                line += f"  // {p['comment']}"
            lines.append(line)
        return '\n'.join(lines)
    
    def format_params_by_cluster(self, params_info: List[Dict], clusters: List[str]) -> str:
        """按簇分组并格式化参数信息"""
        output_lines = []
        
        for cluster_name in clusters:
            cluster_params_list = self.clusters_def.get(cluster_name, [])
            cluster_params_set = set(cluster_params_list)
            
            # 过滤属于该簇的参数
            params_in_cluster = [p for p in params_info if p['name'] in cluster_params_set]
            
            if params_in_cluster:
                output_lines.append(f"\n### 簇: {cluster_name} ({len(params_in_cluster)} 个参数)")
                for p in params_in_cluster:
                    line = f"  - {p['name']} ({p['type']})"
                    if p['default']:
                        line += f" = {p['default']}"
                    if p['range']:
                        line += f" [{p['range']}]"
                    if p['comment']:
                        line += f"  // {p['comment']}"
                    output_lines.append(line)
        
        return '\n'.join(output_lines)
    
    def generate_inter_file_prompt(self, pair: Dict) -> str:
        """生成跨文件分析的提示词（优化版）"""
        
        # 按簇分组显示参数
        caller_params_text = self.format_params_by_cluster(
            pair['caller_params_info'], 
            pair['caller_clusters']
        )
        callee_params_text = self.format_params_by_cluster(
            pair['callee_params_info'], 
            pair['callee_clusters']
        )
        
        prompt = f"""# 硬件参数耦合分析任务

## 背景
这是一个硬件设计项目的配置参数分析。配置参数在编译时确定硬件模块的行为特性（如位宽、深度、使能等）。

## 任务目标
分析两个有模块实例化关系的文件之间，**配置参数的依赖和约束关系**。

---

## 调用者文件（实例化其他模块的文件）
**文件**: `{Path(pair['caller_file']).name}`
**所属参数簇**: {', '.join(pair['caller_clusters'])}

{caller_params_text}

---

## 被调用文件（被实例化的模块文件）
**文件**: `{Path(pair['callee_file']).name}`
**所属参数簇**: {', '.join(pair['callee_clusters'])}

{callee_params_text}

---

## 实例化关系
```
调用者文件实例化了被调用文件中定义的模块
模块类型: {pair['module']}
实例名称: {pair['instance']}
上下文: {pair['instantiation_code']}
```

---

## 分析指导

### 1. 理解硬件参数耦合的常见模式

**A. 直接参数传递 (DIRECT_PASS)**
- 调用者通过实例化参数直接传递给被调用者
- 例如：`top_width` → `fifo_width` (通过 `#(.WIDTH(top_width))`)

**B. 派生计算 (DERIVATION)**
- 一个参数通过数学公式计算得到另一个参数
- 例如：`addr_width = log2(depth)`

**C. 约束关系 (CONSTRAINT)**
- 参数之间必须满足的不等式或等式
- 例如：`input_width <= output_width` (避免数据截断)
- 例如：`cache_line_size % bus_width == 0` (对齐要求)

**D. 条件依赖 (CONDITIONAL)**
- 某参数的值决定另一参数的取值或有效性
- 例如：`if enable_ecc==1 then ecc_width=8 else ecc_width=0`

**E. 资源约束 (RESOURCE)**
- 多个参数共享资源限制
- 例如：`num_channels * channel_width <= total_bandwidth`

**F. 隐式语义依赖 (SEMANTIC)**
- 功能上相关但无显式代码关联
- 例如：发送端的 `packet_size` 应 ≤ 接收端的 `buffer_size`

### 2. 分析步骤
1. 检查调用者是否通过实例化参数传递值给被调用者
2. 识别参数的语义关系（位宽、深度、使能、配置等）
3. 推断隐含的约束条件（如位宽匹配、容量限制等）
4. 判断耦合的强度和置信度

### 3. 置信度评估
- **high**: 代码中有显式关联（如参数传递、计算公式）
- **medium**: 语义上强相关（如数据通路的位宽匹配）
- **low**: 推测性的关系（如可能的资源约束）

---

## 输出要求

**JSON格式**，包含以下字段：

```json
{{
  "has_coupling": true,
  "analysis_summary": "简要总结发现的主要耦合模式（1-2句话）",
  "couplings": [
    {{
      "caller_param": "调用者文件中的参数名",
      "callee_param": "被调用者文件中的参数名",
      "type": "DIRECT_PASS | DERIVATION | CONSTRAINT | CONDITIONAL | RESOURCE | SEMANTIC",
      "description": "用一句话清晰描述这个耦合关系",
      "rule": "形式化规则（如 A=B, A>=B, if A then B, A=log2(B)）",
      "confidence": "high | medium | low",
      "reasoning": "为什么认为存在这个耦合（简短说明）"
    }}
  ]
}}

**注意**：
- 只输出JSON，不要其他解释文字
- 如果找不到任何耦合，返回 `{{"has_coupling": false, "couplings": []}}`
- 聚焦于**实际存在的、有意义的**耦合关系，避免臆测
- 优先标注高置信度的耦合

---

请开始分析。
""" 
        return prompt
    
    def generate_intra_file_prompt(self, pair: Dict) -> str:
        """生成单文件多簇分析的提示词（优化版）"""
        
        # 按簇分组显示参数
        params_by_cluster = self.format_params_by_cluster(
            pair['params_info'], 
            pair['clusters']
        )
        
        prompt = f"""# 单文件内跨簇参数耦合分析

## 背景
这是一个硬件设计项目的配置参数分析。一个文件可能包含多个功能模块的参数，这些参数被分到了不同的**参数簇**中。

## 任务目标
分析同一个文件内，**不同参数簇之间的参数耦合关系**。

---

## 目标文件
**文件**: `{Path(pair['file']).name}`
**在调用链中的角色**: {'调用者 (实例化其他模块)' if pair['role'] == 'caller' else '被调用者 (被其他模块实例化)'}
**关联文件**: `{Path(pair['other_file']).name}`

## 参数分布
该文件包含 **{len(pair['clusters'])} 个参数簇**，共 **{len(pair['params'])} 个参数**：

{params_by_cluster}

## 实例化上下文
```
模块类型: {pair['module']}
实例名称: {pair['instance']}
说明: {pair['instantiation_code']}
```

---

## 分析指导

### 1. 理解跨簇耦合的场景

在硬件设计中，不同功能模块（簇）的参数可能存在隐式约束：

**场景A: 数据通路一致性**
- 例如：时钟簇的 `clk_freq` 影响 FIFO簇的 `depth`（满足吞吐需求）

**场景B: 资源共享约束**
- 例如：多个DMA通道的总带宽不能超过总线带宽

**场景C: 层次化派生**
- 例如：顶层参数 `total_width` 决定了子模块的 `channel_width`

**场景D: 使能开关联动**
- 例如：`enable_feature_A==1` 时要求 `feature_B_buffer_size >= 1024`

### 2. 分析步骤
1. **识别簇的语义**：理解每个簇代表的功能模块
2. **检查参数类型**：位宽、深度、使能、频率等
3. **推断依赖链**：是否存在"簇A影响簇B"的关系
4. **评估独立性**：哪些簇之间确实无关联

### 3. 重点关注
- 不同簇的参数是否在同一数据通路上（位宽需匹配）
- 是否共享资源（总带宽、总面积等）
- 是否存在功能依赖（一个簇的使能影响另一簇的配置）

---

## 输出要求

**JSON格式**：

```json
{{
  "has_coupling": true,
  "cluster_analysis": [
    {{
      "cluster1": "簇名称1",
      "cluster2": "簇名称2",
      "relationship": "COUPLED | INDEPENDENT",
      "summary": "一句话说明这两个簇的关系"
    }}
  ],
  "couplings": [
    {{
      "param1": "来自簇1的参数名",
      "param2": "来自簇2的参数名",
      "param1_cluster": "簇名称1",
      "param2_cluster": "簇名称2",
      "type": "CROSS_CLUSTER_CONSTRAINT | CROSS_CLUSTER_CONDITIONAL | CROSS_CLUSTER_DERIVATION",
      "description": "清晰描述跨簇耦合关系",
      "rule": "形式化规则",
      "confidence": "high | medium | low",
      "reasoning": "为什么认为存在跨簇耦合"
    }}
  ]
}}

**注意**：
- 只输出JSON
- 如果所有簇都独立，也要在 `cluster_analysis` 中明确标注
- 不要臆测过度，聚焦于实际可能存在的耦合

---

请开始分析。
"""
        return prompt
    
    def generate_prompt(self, pair: Dict) -> str:
        """根据类型生成对应的提示词"""
        if pair['type'] == 'INTER_FILE':
            return self.generate_inter_file_prompt(pair)
        elif pair['type'] == 'INTRA_FILE_MULTI_CLUSTER':
            return self.generate_intra_file_prompt(pair)
        else:
            raise ValueError(f"未知的分析类型: {pair['type']}")
    
    def call_llm(self, prompt: str, model: str = "claude-3-5-sonnet-20241022"):
        """
        调用LLM API
        TODO: 实现实际的API调用逻辑
        """
        # 这里需要根据您使用的LLM API进行实现
        # 示例框架：
        # import anthropic
        # client = anthropic.Anthropic(api_key="your-api-key")
        # response = client.messages.create(
        #     model=model,
        #     max_tokens=2048,
        #     messages=[{"role": "user", "content": prompt}]
        # )
        # return json.loads(response.content[0].text)
        pass

    def analyze_all(self, max_pairs: int = None):
        """分析所有文件对"""
        
        print("="*70)
        print("🤖 Step 4: LLM 分析")
        print("="*70)
        
        pairs_to_analyze = self.file_pairs[:max_pairs] if max_pairs else self.file_pairs
        
        # 统计任务类型
        inter_file_pairs = [p for p in pairs_to_analyze if p['type'] == 'INTER_FILE']
        intra_file_pairs = [p for p in pairs_to_analyze if p['type'] == 'INTRA_FILE_MULTI_CLUSTER']
        
        print(f"准备分析 {len(pairs_to_analyze)} 个任务:")
        print(f"  - 跨文件分析: {len(inter_file_pairs)}")
        print(f"  - 单文件多簇分析: {len(intra_file_pairs)}\n")
        
        results = []
        
        for i, pair in enumerate(pairs_to_analyze, 1):
            if pair['type'] == 'INTER_FILE':
                caller_name = Path(pair['caller_file']).name
                callee_name = Path(pair['callee_file']).name
                print(f"[{i}/{len(pairs_to_analyze)}] 跨文件: {caller_name} → {callee_name}", end=' ')
            else:
                file_name = Path(pair['file']).name
                cluster_info = f"{len(pair['clusters'])} 簇"
                print(f"[{i}/{len(pairs_to_analyze)}] 单文件多簇: {file_name} ({cluster_info})", end=' ')
            
            prompt = self.generate_prompt(pair)
            
            try:
                analysis = self.call_llm(prompt)
                
                if analysis and analysis.get('has_coupling'):
                    coupling_count = len(analysis.get('couplings', []))
                    print(f"✅ 发现 {coupling_count} 个耦合")
                    
                    results.append({
                        'task': pair,
                        'analysis': analysis
                    })
                else:
                    print("➖ 无耦合")
            
            except Exception as e:
                print(f"❌ 错误: {e}")
        
        print(f"\n✅ LLM分析完成，共 {len(results)} 个任务发现耦合\n")
        
        return results
    
class CouplingExtractor:
    """从LLM结果提取耦合关系"""
    def __init__(self, llm_results: List[Dict]):
        self.llm_results = llm_results
    
    def extract(self) -> List[Dict]:
        """提取所有耦合关系"""
        
        print("="*70)
        print("📊 Step 5: 提取耦合关系")
        print("="*70)
        
        all_couplings = []
        
        for result in self.llm_results:
            task = result['task']
            analysis = result['analysis']
            
            if task['type'] == 'INTER_FILE':
                # 跨文件耦合
                for c in analysis.get('couplings', []):
                    coupling = {
                        'scope': 'INTER_FILE',
                        'param1': c.get('caller_param'),
                        'param2': c.get('callee_param'),
                        'cluster1': task.get('caller_clusters', []),
                        'cluster2': task.get('callee_clusters', []),
                        'type': c.get('type'),
                        'description': c.get('description'),
                        'rule': c.get('rule'),
                        'confidence': c.get('confidence', 'medium'),
                        'evidence': {
                            'caller_file': task['caller_file'],
                            'callee_file': task['callee_file'],
                            'module': task['module'],
                            'instance': task['instance']
                        }
                    }
                    all_couplings.append(coupling)
            
            elif task['type'] == 'INTRA_FILE_MULTI_CLUSTER':
                # 单文件多簇耦合
                for c in analysis.get('couplings', []):
                    coupling = {
                        'scope': 'INTRA_FILE_CROSS_CLUSTER',
                        'param1': c.get('param1'),
                        'param2': c.get('param2'),
                        'cluster1': c.get('param1_cluster'),
                        'cluster2': c.get('param2_cluster'),
                        'type': c.get('type'),
                        'description': c.get('description'),
                        'rule': c.get('rule'),
                        'confidence': c.get('confidence', 'medium'),
                        'evidence': {
                            'file': task['file'],
                            'role': task['role'],
                            'other_file': task['other_file'],
                            'module': task['module'],
                            'instance': task['instance']
                        }
                    }
                    all_couplings.append(coupling)
        
        print(f"✅ 提取到 {len(all_couplings)} 条耦合关系\n")
        
        return all_couplings

    def build_graph(self, couplings: List[Dict]) -> nx.DiGraph:
        """构建耦合关系图"""
        
        G = nx.DiGraph()
        
        for c in couplings:
            p1 = c['param1']
            p2 = c['param2']
            
            if p1 and p2:
                G.add_edge(
                    p1, p2,
                    type=c['type'],
                    description=c['description'],
                    rule=c['rule'],
                    confidence=c['confidence']
                )
        
        return G

    def generate_summary(self, couplings: List[Dict]) -> Dict:
        """生成统计摘要"""
        
        type_counts = defaultdict(int)
        scope_counts = defaultdict(int)
        conf_counts = defaultdict(int)
        
        for c in couplings:
            type_counts[c['type']] += 1
            scope_counts[c['scope']] += 1
            conf_counts[c['confidence']] += 1
        
        return {
            'total_couplings': len(couplings),
            'unique_params': len(set([c['param1'] for c in couplings] + [c['param2'] for c in couplings])),
            'by_type': dict(type_counts),
            'by_scope': dict(scope_counts),
            'by_confidence': dict(conf_counts),
            'inter_file_couplings': sum(1 for c in couplings if c['scope'] == 'INTER_FILE'),
            'cross_cluster_couplings': sum(1 for c in couplings if c['scope'] == 'INTRA_FILE_CROSS_CLUSTER')
        }
    
def main(): 
    """主流程"""
    print("\n" + "="*70)
    print("🚀 参数耦合关系分析系统")
    print("="*70 + "\n")

    # 配置路径
    dependency_json = Path("dependency_analysis.json")
    clusters_json = Path("clusters.json")
    params_file = Path("cfg_params/fdae_top_template.in_pdt")
    driver_root = Path("driver/")

    # Step 1: 加载数据（包括完整参数配置）
    loader = DependencyLoader(dependency_json, clusters_json, params_file)
    loader.load()

    # Step 2: 字符串匹配（附加完整参数信息）
    matcher = StringMatcher(driver_root, loader.clusters, loader.params_info)
    candidates = matcher.scan_all()

    # 保存candidates
    with open('candidates.json', 'w', encoding='utf-8') as f:
        json.dump(candidates, f, indent=2, ensure_ascii=False)
    print("💾 已保存: candidates.json\n")

    # Step 3: 构建文件对（支持单端多簇和双端分析）
    pair_builder = FilePairBuilder(loader.dependency_data, candidates)
    file_pairs = pair_builder.build_pairs()

    # 保存file_pairs
    with open('file_pairs.json', 'w', encoding='utf-8') as f:
        json.dump(file_pairs, f, indent=2, ensure_ascii=False)
    print("💾 已保存: file_pairs.json\n")

    # Step 4: LLM分析（传入簇定义用于分组显示）
    llm_analyzer = LLMCouplingAnalyzer(file_pairs, loader.clusters)
    # 测试：只分析前5对
    # llm_results = llm_analyzer.analyze_all(max_pairs=5)
    # 完整分析：
    llm_results = llm_analyzer.analyze_all()

    # 保存LLM结果
    with open('coupling_analysis_results.json', 'w', encoding='utf-8') as f:
        json.dump(llm_results, f, indent=2, ensure_ascii=False)
    print("💾 已保存: coupling_analysis_results.json\n")

    # Step 5: 提取耦合关系
    extractor = CouplingExtractor(llm_results)
    couplings = extractor.extract()

    # 保存耦合关系
    with open('extracted_param_couplings.json', 'w', encoding='utf-8') as f:
        json.dump(couplings, f, indent=2, ensure_ascii=False)
    print("💾 已保存: extracted_param_couplings.json\n")

    # 生成摘要
    summary = extractor.generate_summary(couplings)
    with open('param_couplings_summary.json', 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print("💾 已保存: param_couplings_summary.json\n")

    # 构建图
    graph = extractor.build_graph(couplings)
    nx.write_gexf(graph, 'coupling_graph.gexf')
    print("💾 已保存: coupling_graph.gexf\n")

    # 打印最终统计
    print("="*70)
    print("📊 最终统计")
    print("="*70)
    print(f"总耦合数: {summary['total_couplings']}")
    print(f"涉及参数: {summary['unique_params']}")
    print(f"\n按范围:")
    print(f"  - 跨文件耦合: {summary['inter_file_couplings']}")
    print(f"  - 跨簇耦合 (单文件): {summary['cross_cluster_couplings']}")
    print(f"\n按类型:")
    for t, count in summary['by_type'].items():
        print(f"  - {t}: {count}")
    print(f"\n按置信度:")
    for conf, count in summary['by_confidence'].items():
        print(f"  - {conf}: {count}")
    print("\n" + "="*70)
    print("✅ 分析完成！")
    print("="*70)

if __name__ == '__main__': 
    main()