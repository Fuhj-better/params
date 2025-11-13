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
# Step 3: 基于依赖关系构建簇对（改造版）
# =============================================================================

class ClusterPairBuilder:
    """构建簇对分析任务(更精细的粒度)"""
    
    def __init__(self, dependency_data: Dict, candidates: Dict, clusters_def: Dict):
        self.dependency_data = dependency_data
        self.candidates = candidates
        self.clusters_def = clusters_def
    
    def build_pairs(self) -> List[Dict]:
        """生成两两簇对的分析任务(去重版 + 过滤未使用的簇)"""
        
        print("="*70)
        print("🔗 Step 3: 构建簇对分析任务（以簇对为中心）")
        print("="*70)
        
        # 首先构建文件->参数映射（按簇分组）
        file_to_params = self._build_file_param_mapping()
        
        # 识别实际被使用的簇
        used_clusters = set()
        for file_data in file_to_params.values():
            used_clusters.update(file_data['clusters'])
        
        # 统计未使用的簇
        all_clusters = set(self.clusters_def.keys())
        unused_clusters = all_clusters - used_clusters
        
        print(f"📊 参数簇统计:")
        print(f"   - 定义的簇总数: {len(all_clusters)}")
        print(f"   - 实际使用的簇: {len(used_clusters)}")
        print(f"   - 未使用的簇: {len(unused_clusters)}")
        
        if unused_clusters:
            print(f"\n⚠️  以下簇未在代码中使用，将被跳过:")
            for cluster in sorted(unused_clusters):
                print(f"      - {cluster}")
        print()
        
        # 只枚举被使用的簇对
        from itertools import combinations
        used_cluster_list = sorted(list(used_clusters))
        all_possible_pairs = list(combinations(used_cluster_list, 2))
        
        print(f"📊 基于 {len(used_clusters)} 个使用中的簇:")
        print(f"   - 理论簇对数: {len(all_possible_pairs)}\n")
        
        # 使用字典存储簇对，键为规范化的簇对标识
        cluster_pair_dict = {}
        
        # 为每个簇对收集代码上下文
        for cluster1, cluster2 in all_possible_pairs:
            contexts = self._collect_contexts_for_cluster_pair(
                cluster1, cluster2, file_to_params
            )
            
            if contexts:  # 只保留有代码上下文的簇对
                pair_key = self._make_cluster_pair_key(cluster1, cluster2)
                cluster_pair_dict[pair_key] = {
                    'cluster_pair': (cluster1, cluster2),
                    'contexts': contexts,
                    'context_count': len(contexts),
                    'has_intra_file': any(c['type'] == 'INTRA_FILE' for c in contexts),
                    'has_inter_file': any(c['type'] == 'INTER_FILE' for c in contexts)
                }
        
        cluster_pairs = list(cluster_pair_dict.values())
        
        # 统计
        print(f"✅ 构建了 {len(cluster_pairs)} 个有代码上下文的簇对")
        print(f"   - 仅单文件内共现: {sum(1 for p in cluster_pairs if p['has_intra_file'] and not p['has_inter_file'])}")
        print(f"   - 仅跨文件依赖: {sum(1 for p in cluster_pairs if p['has_inter_file'] and not p['has_intra_file'])}")
        print(f"   - 两者都有: {sum(1 for p in cluster_pairs if p['has_intra_file'] and p['has_inter_file'])}")
        print(f"   - 无代码关联的簇对: {len(all_possible_pairs) - len(cluster_pairs)} (已过滤)\n")
        
        return cluster_pairs
    
    def _build_file_param_mapping(self) -> Dict:
        """构建文件->参数映射（按簇分组）"""
        file_to_params = {}
        
        for cluster_name, files in self.candidates.items():
            # 跳过没有匹配文件的簇
            if not files:
                continue
                
            for f in files:
                fp = f['file']
                if fp not in file_to_params:
                    file_to_params[fp] = {
                        'clusters': set(),
                        'params_by_cluster': {}
                    }
                
                file_to_params[fp]['clusters'].add(cluster_name)
                file_to_params[fp]['params_by_cluster'][cluster_name] = {
                    'params': f['matched_params'],
                    'params_info': f['params_info'],
                    'contexts': f.get('contexts', [])
                }
        
        return file_to_params
    
    def _collect_contexts_for_cluster_pair(self, 
                                           cluster1: str, 
                                           cluster2: str,
                                           file_to_params: Dict) -> List[Dict]:
        """为指定簇对收集所有代码上下文"""
        contexts = []
        
        # 1. 单文件内共现
        for file_path, file_data in file_to_params.items():
            file_clusters = file_data['clusters']
            
            if cluster1 in file_clusters and cluster2 in file_clusters:
                contexts.append({
                    'type': 'INTRA_FILE',
                    'file': file_path,
                    'cluster1': cluster1,
                    'cluster2': cluster2,
                    'cluster1_params': file_data['params_by_cluster'][cluster1],
                    'cluster2_params': file_data['params_by_cluster'][cluster2]
                })
        
        # 2. 跨文件依赖
        module_deps = (self.dependency_data
                      .get('dependency_analysis', {})
                      .get('dependency_relationships', {})
                      .get('module_dependencies', []))
        
        for dep in module_deps:
            caller = dep.get('source_path')
            callee = dep.get('target_path')
            
            caller_info = file_to_params.get(caller)
            callee_info = file_to_params.get(callee)
            
            if not (caller_info and callee_info):
                continue
            
            caller_clusters = caller_info['clusters']
            callee_clusters = callee_info['clusters']
            
            # 情况1: caller有cluster1, callee有cluster2
            if cluster1 in caller_clusters and cluster2 in callee_clusters:
                contexts.append({
                    'type': 'INTER_FILE',
                    'caller_file': caller,
                    'callee_file': callee,
                    'caller_cluster': cluster1,
                    'callee_cluster': cluster2,
                    'caller_params': caller_info['params_by_cluster'][cluster1],
                    'callee_params': callee_info['params_by_cluster'][cluster2],
                    'module': dep.get('module_type'),
                    'instance': dep.get('instance_name'),
                    'instantiation_code': dep.get('description', ''),
                    'direction': f'{cluster1}→{cluster2}'
                })
            
            # 情况2: caller有cluster2, callee有cluster1（反向）
            if cluster2 in caller_clusters and cluster1 in callee_clusters:
                contexts.append({
                    'type': 'INTER_FILE',
                    'caller_file': caller,
                    'callee_file': callee,
                    'caller_cluster': cluster2,
                    'callee_cluster': cluster1,
                    'caller_params': caller_info['params_by_cluster'][cluster2],
                    'callee_params': callee_info['params_by_cluster'][cluster1],
                    'module': dep.get('module_type'),
                    'instance': dep.get('instance_name'),
                    'instantiation_code': dep.get('description', ''),
                    'direction': f'{cluster2}→{cluster1}'
                })
        
        return contexts
    
    def _make_cluster_pair_key(self, cluster1: str, cluster2: str) -> str:
        """生成簇对的唯一键（无序）"""
        c1, c2 = sorted([cluster1, cluster2])
        return f"{c1}↔{c2}"
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

# =============================================================================
# Step 4: LLM Analysis - Cluster Pair Centric Approach
# =============================================================================

class LLMCouplingAnalyzer:
    """Analyze parameter coupling between cluster pairs using LLM"""
    
    def __init__(self, cluster_pairs: List[Dict], clusters_def: Dict):
        """
        Args:
            cluster_pairs: List of cluster pair tasks from ClusterPairBuilder
            clusters_def: Original cluster definitions (cluster_name -> param_list)
        """
        self.cluster_pairs = cluster_pairs
        self.clusters_def = clusters_def
    
    def _format_params_info(self, params_info: List[Dict]) -> str:
        """Format parameter information into readable text"""
        lines = []
        for p in params_info:
            line = f"- **{p['name']}** ({p['type']})"
            if p.get('default'):
                line += f" = {p['default']}"
            if p.get('range'):
                line += f" [{p['range']}]"
            if p.get('comment'):
                line += f"  // {p['comment']}"
            lines.append(line)
        return '\n'.join(lines) if lines else "  (No parameters)"
    
    def generate_prompt(self, pair_task: Dict) -> str:
        """Generate LLM prompt for analyzing a cluster pair
        
        This method aggregates all code contexts where the cluster pair appears
        """
        cluster1, cluster2 = pair_task['cluster_pair']
        contexts = pair_task['contexts']
        
        # Get parameter definitions from the first context
        first_ctx = contexts[0]
        if first_ctx['type'] == 'INTRA_FILE':
            cluster1_params = first_ctx['cluster1_params']['params_info']
            cluster2_params = first_ctx['cluster2_params']['params_info']
        else:  # INTER_FILE
            cluster1_params = first_ctx['caller_params']['params_info']
            cluster2_params = first_ctx['callee_params']['params_info']
        
        prompt = f"""# Hardware Parameter Cluster Coupling Analysis

## Objective
Analyze the coupling relationships between two parameter clusters:
- **Cluster 1**: {cluster1}
- **Cluster 2**: {cluster2}

---

## Cluster 1 Parameter Definitions: {cluster1}
{self._format_params_info(cluster1_params)}

---

## Cluster 2 Parameter Definitions: {cluster2}
{self._format_params_info(cluster2_params)}

---

## Code Contexts

This cluster pair appears in **{len(contexts)}** code context(s):

"""
        
        # List all contexts
        for i, ctx in enumerate(contexts, 1):
            if ctx['type'] == 'INTRA_FILE':
                prompt += f"""
### Context {i}: Intra-File Co-occurrence
- **File**: `{Path(ctx['file']).name}`
- **Description**: This file uses parameters from both clusters
- **{cluster1} parameter count**: {len(ctx['cluster1_params']['params'])}
- **{cluster2} parameter count**: {len(ctx['cluster2_params']['params'])}
- **Sample {cluster1} params**: {', '.join(ctx['cluster1_params']['params'][:5])}{'...' if len(ctx['cluster1_params']['params']) > 5 else ''}
- **Sample {cluster2} params**: {', '.join(ctx['cluster2_params']['params'][:5])}{'...' if len(ctx['cluster2_params']['params']) > 5 else ''}
"""
            else:  # INTER_FILE
                prompt += f"""
### Context {i}: Inter-File Dependency
- **Calling relationship**: `{Path(ctx['caller_file']).name}` → `{Path(ctx['callee_file']).name}`
- **Cluster direction**: {ctx['direction']}
- **Module type**: {ctx['module']}
- **Instance name**: {ctx['instance']}
- **Instantiation code snippet**: 
  ```verilog
  {ctx['instantiation_code'][:300]}   
  """
        prompt += """
## Analysis Guidelines
1. Common Hardware Parameter Coupling Patterns
A. DIRECT_PASS (Direct Parameter Passing)

Caller passes parameter value to callee through instantiation
Example: top_width → fifo_width (via #(.WIDTH(top_width)))
B. DERIVATION (Derived Calculation)

One parameter is mathematically derived from another
Example: addr_width = log2(depth)
C. CONSTRAINT (Constraint Relationship)

Parameters must satisfy inequalities or equations
Example: input_width <= output_width (avoid data truncation)
Example: cache_line_size % bus_width == 0 (alignment requirement)
D. CONDITIONAL (Conditional Dependency)

One parameter's value determines another's validity or value
Example: if enable_ecc==1 then ecc_width=8 else ecc_width=0
E. RESOURCE (Resource Constraint)

Multiple parameters share resource limitations
Example: num_channels * channel_width <= total_bandwidth
F. SEMANTIC (Implicit Semantic Dependency)

Functionally related but no explicit code association
Example: sender's packet_size should ≤ receiver's buffer_size
2. Analysis Steps
Check if caller passes values to callee through instantiation parameters
Identify semantic relationships (width, depth, enable, configuration, etc.)
Infer implicit constraints (e.g., width matching, capacity limits)
Judge coupling strength and confidence level
3. Confidence Assessment
high: Explicit association in code (e.g., parameter passing, calculation formula)
medium: Strong semantic correlation (e.g., data path width matching)
low: Speculative relationship (e.g., possible resource constraints)
## Analysis Task
Please synthesize all the above code contexts and analyze the coupling relationship between these two parameter clusters.

Focus on:

Existence of coupling: Are there dependencies or constraints between parameters from the two clusters?
Coupling types: Identify the pattern(s) listed above
Specific parameter pairs: List all discovered parameter coupling pairs
## Output Format
Output ONLY JSON, no other text

JSON
{{
  "cluster_pair": ["{cluster1}", "{cluster2}"],
  "has_coupling": true,
  "analysis_summary": "One sentence summarizing the relationship between these two clusters",
  "couplings": [
    {{
      "param1": "Parameter name from {cluster1}",
      "param2": "Parameter name from {cluster2}",
      "param1_cluster": "{cluster1}",
      "param2_cluster": "{cluster2}",
      "type": "DIRECT_PASS | DERIVATION | CONSTRAINT | CONDITIONAL | RESOURCE | SEMANTIC",
      "description": "Clear description of this coupling relationship",
      "rule": "Formalized rule (e.g., A=B, A>=B, A=log2(B))",
      "confidence": "high | medium | low",
      "reasoning": "Brief explanation of why this coupling exists",
      "evidence_contexts": [1, 2]
    }}
  ]
}}
Notes:

If no coupling found, return {{"has_coupling": false, "cluster_pair": ["{cluster1}", "{cluster2}"], "couplings": []}}

evidence_contexts indicates which context numbers support this coupling

Focus on actually existing, meaningful coupling relationships, avoid speculation

Prioritize high-confidence couplings """ 
        return prompt
    def analyze_all(self, max_pairs: int = None): 
        """Analyze all cluster pairs"""
        print("="*70)
        print("🤖 Step 4: LLM Analysis - Cluster Pairs")
        print("="*70)
        
        pairs_to_analyze = self.cluster_pairs[:max_pairs] if max_pairs else self.cluster_pairs
        
        print(f"Preparing to analyze {len(pairs_to_analyze)} cluster pair(s)\n")
        
        results = []
        
        for i, pair_task in enumerate(pairs_to_analyze, 1):
            cluster1, cluster2 = pair_task['cluster_pair']
            context_count = pair_task['context_count']
            
            print(f"[{i}/{len(pairs_to_analyze)}] Analyzing: ({cluster1}, {cluster2})")
            print(f"           Contexts: {context_count}", end=' ')
            
            if context_count == 0:
                print("⚠️  No contexts (skipped)")
                continue
            
            prompt = self.generate_prompt(pair_task)
            
            try:
                analysis = self.call_llm(prompt)
                
                if analysis and analysis.get('has_coupling'):
                    coupling_count = len(analysis.get('couplings', []))
                    print(f"✅ Found {coupling_count} coupling(s)")
                    
                    results.append({
                        'cluster_pair': pair_task['cluster_pair'],
                        'contexts': pair_task['contexts'],
                        'context_count': context_count,
                        'has_intra_file': pair_task['has_intra_file'],
                        'has_inter_file': pair_task['has_inter_file'],
                        'analysis': analysis
                    })
                else:
                    print(f"➖ No coupling")
            
            except Exception as e:
                print(f"❌ Error: {e}")
        
        print(f"\n✅ LLM analysis completed: {len(results)} cluster pair(s) with coupling found\n")
        
        return results
class CouplingExtractor:
    """从LLM结果提取耦合关系"""
    def __init__(self, llm_results: List[Dict]):
        self.llm_results = llm_results

    def extract(self) -> List[Dict]:
        """Extract all parameter-level couplings with cluster information"""
        
        print("="*70)
        print("📋 Step 6: Extracting Parameter-Level Couplings")
        print("="*70)
        
        all_couplings = []
        
        for result in self.llm_results:
            cluster1, cluster2 = result['cluster_pair']
            analysis = result['analysis']
            contexts = result['contexts']
            
            for coupling in analysis.get('couplings', []):
                # Enrich coupling with cluster and context information
                enriched_coupling = {
                    'param1': coupling.get('param1'),
                    'param2': coupling.get('param2'),
                    'param1_cluster': coupling.get('param1_cluster', cluster1),
                    'param2_cluster': coupling.get('param2_cluster', cluster2),
                    'type': coupling.get('type'),
                    'description': coupling.get('description'),
                    'rule': coupling.get('rule'),
                    'confidence': coupling.get('confidence', 'medium'),
                    'reasoning': coupling.get('reasoning', ''),
                    'evidence_contexts': coupling.get('evidence_contexts', []),
                    'context_count': result['context_count'],
                    'has_intra_file': result['has_intra_file'],
                    'has_inter_file': result['has_inter_file']
                }
                
                all_couplings.append(enriched_coupling)
        
        print(f"✅ Extracted {len(all_couplings)} parameter coupling(s)\n")
        
        return all_couplings

    def build_graph(self, couplings: List[Dict]) -> nx.DiGraph:
        """Build parameter coupling graph"""
        
        G = nx.DiGraph()
        
        for c in couplings:
            p1 = c['param1']
            p2 = c['param2']
            
            if p1 and p2:
                # Add nodes with cluster information
                G.add_node(p1, cluster=c['param1_cluster'])
                G.add_node(p2, cluster=c['param2_cluster'])
                
                # Add edge with coupling information
                G.add_edge(
                    p1, p2,
                    type=c['type'],
                    description=c['description'],
                    rule=c['rule'],
                    confidence=c['confidence']
                )
        
        return G

    def generate_summary(self, couplings: List[Dict]) -> Dict:
        """Generate statistical summary"""
        
        type_counts = defaultdict(int)
        conf_counts = defaultdict(int)
        cluster_pair_counts = defaultdict(int)
        
        for c in couplings:
            type_counts[c['type']] += 1
            conf_counts[c['confidence']] += 1
            
            # Count cluster pair combinations
            c1 = c['param1_cluster']
            c2 = c['param2_cluster']
            pair_key = tuple(sorted([c1, c2]))
            cluster_pair_counts[pair_key] += 1
        
        unique_params = set()
        for c in couplings:
            if c['param1']:
                unique_params.add(c['param1'])
            if c['param2']:
                unique_params.add(c['param2'])
        
        return {
            'total_couplings': len(couplings),
            'unique_parameters': len(unique_params),
            'unique_cluster_pairs': len(cluster_pair_counts),
            'by_type': dict(type_counts),
            'by_confidence': dict(conf_counts),
            'by_cluster_pair': dict(cluster_pair_counts),
            'high_confidence_count': conf_counts.get('high', 0),
            'medium_confidence_count': conf_counts.get('medium', 0),
            'low_confidence_count': conf_counts.get('low', 0)
        }


def build_coupling_matrix(llm_results: List[Dict], clusters_def: Dict, used_clusters: Set[str] = None) -> Dict:
    """Build cluster-to-cluster coupling matrix from LLM results
    
    Args:
        llm_results: Results from LLMCouplingAnalyzer.analyze_all()
        clusters_def: Original cluster definitions
        used_clusters: Set of actually used clusters (optional)
    
    Returns:
        Nested dict: cluster1 -> cluster2 -> coupling info
    """
    
    print("="*70)
    print("📊 Step 5: Building Cluster Coupling Matrix")
    print("="*70)
    
    # 如果没有提供used_clusters，则从llm_results中推断
    if used_clusters is None:
        used_clusters = set()
        for result in llm_results:
            c1, c2 = result['cluster_pair']
            used_clusters.add(c1)
            used_clusters.add(c2)
    
    # 只为使用过的簇构建矩阵
    cluster_list = sorted(list(used_clusters))
    
    # Initialize matrix (only for used clusters)
    matrix = {
        c1: {
            c2: {
                'has_coupling': False,
                'coupling_count': 0,
                'context_count': 0
            } 
            for c2 in cluster_list
        }
        for c1 in cluster_list
    }
    
    # Fill in results
    for result in llm_results:
        c1, c2 = result['cluster_pair']
        analysis = result['analysis']
        couplings = analysis.get('couplings', [])
        
        coupling_info = {
            'has_coupling': True,
            'coupling_count': len(couplings),
            'context_count': result['context_count'],
            'has_intra_file': result['has_intra_file'],
            'has_inter_file': result['has_inter_file'],
            'summary': analysis.get('analysis_summary', ''),
            'couplings': couplings
        }
        
        # Symmetric fill (since cluster pairs are unordered)
        matrix[c1][c2] = coupling_info
        matrix[c2][c1] = coupling_info
    
    # Generate statistics
    total_pairs_analyzed = len(llm_results)
    total_couplings = sum(r['analysis'].get('couplings', []) for r in llm_results)
    total_couplings_count = sum(len(couplings) for couplings in total_couplings)
    
    print(f"✅ Matrix built:")
    print(f"   Used clusters: {len(cluster_list)}")
    print(f"   Total defined clusters: {len(clusters_def)}")
    print(f"   Analyzed cluster pairs: {total_pairs_analyzed}")
    print(f"   Coupled cluster pairs: {total_pairs_analyzed}")
    print(f"   Total parameter couplings: {total_couplings_count}\n")
    
    return matrix
def main(): 
    """主流程 - 簇对中心分析"""
    print("\n" + "="*70)
    print("🚀 参数耦合关系分析系统 (Cluster Pair Centric)")
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

    # Step 3: 构建簇对任务（改造版 - 以簇对为中心，自动过滤未使用的簇）
    cluster_pair_builder = ClusterPairBuilder(
        loader.dependency_data, 
        candidates,
        loader.clusters  # 传入簇定义
    )
    cluster_pairs = cluster_pair_builder.build_pairs()

    # 保存簇对任务（简化版，用于查看）
    with open('cluster_pair_tasks.json', 'w', encoding='utf-8') as f:
        # 只保存关键信息，避免嵌套对象序列化问题
        simplified_tasks = []
        for task in cluster_pairs:
            simplified_tasks.append({
                'cluster_pair': task['cluster_pair'],
                'context_count': task['context_count'],
                'has_intra_file': task['has_intra_file'],
                'has_inter_file': task['has_inter_file']
            })
        json.dump(simplified_tasks, f, indent=2, ensure_ascii=False)
    print("💾 已保存: cluster_pair_tasks.json\n")

    # Step 4: LLM分析簇对（改造版）
    llm_analyzer = LLMCouplingAnalyzer(cluster_pairs, loader.clusters)
    
    # 测试模式：只分析前5对
    # llm_results = llm_analyzer.analyze_all(max_pairs=5)
    
    # 完整分析模式：
    llm_results = llm_analyzer.analyze_all()

    # 保存LLM分析结果
    with open('cluster_pair_couplings.json', 'w', encoding='utf-8') as f:
        # 序列化时处理可能的复杂对象
        json.dump(llm_results, f, indent=2, ensure_ascii=False, default=str)
    print("💾 已保存: cluster_pair_couplings.json\n")

    # Step 5: 构建簇对耦合矩阵（只包含使用的簇）
    coupling_matrix = build_coupling_matrix(llm_results, loader.clusters)
    
    with open('cluster_coupling_matrix.json', 'w', encoding='utf-8') as f:
        json.dump(coupling_matrix, f, indent=2, ensure_ascii=False)
    print("💾 已保存: cluster_coupling_matrix.json\n")

    # Step 6: 提取参数级别的耦合（可选）
    extractor = CouplingExtractor(llm_results)
    param_couplings = extractor.extract()

    # 保存参数级别的耦合
    with open('extracted_param_couplings.json', 'w', encoding='utf-8') as f:
        json.dump(param_couplings, f, indent=2, ensure_ascii=False)
    print("💾 已保存: extracted_param_couplings.json\n")

    # 生成统计摘要
    summary = extractor.generate_summary(param_couplings)
    with open('param_couplings_summary.json', 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print("💾 已保存: param_couplings_summary.json\n")

    # 构建参数耦合图
    graph = extractor.build_graph(param_couplings)
    nx.write_gexf(graph, 'coupling_graph.gexf')
    print("💾 已保存: coupling_graph.gexf\n")

    # 打印最终统计
    print("="*70)
    print("📊 最终统计")
    print("="*70)
    
    # 簇对级别统计
    total_cluster_pairs = len(cluster_pairs)
    coupled_cluster_pairs = len(llm_results)
    
    print(f"\n【簇对级别统计】")
    print(f"  分析的簇对数: {total_cluster_pairs}")
    if total_cluster_pairs > 0:
        print(f"  有耦合的簇对: {coupled_cluster_pairs} ({coupled_cluster_pairs/total_cluster_pairs*100:.1f}%)")
    else:
        print(f"  有耦合的簇对: 0")
    
    # 参数级别统计
    print(f"\n【参数级别统计】")
    print(f"  总参数耦合数: {summary['total_couplings']}")
    print(f"  涉及参数数量: {summary['unique_parameters']}")
    print(f"  涉及簇对数量: {summary['unique_cluster_pairs']}")
    
    print(f"\n【按耦合类型】")
    for coupling_type, count in summary['by_type'].items():
        print(f"  - {coupling_type}: {count}")
    
    print(f"\n【按置信度】")
    print(f"  - High: {summary['high_confidence_count']}")
    print(f"  - Medium: {summary['medium_confidence_count']}")
    print(f"  - Low: {summary['low_confidence_count']}")
    
    # Top 5 最多耦合的簇对
    if summary['by_cluster_pair']:
        print(f"\n【耦合最多的簇对 Top 5】")
        top_pairs = sorted(
            summary['by_cluster_pair'].items(), 
            key=lambda x: x[1], 
            reverse=True
        )[:5]
        for i, (pair, count) in enumerate(top_pairs, 1):
            print(f"  {i}. {pair[0]} ↔ {pair[1]}: {count} 个耦合")
    
    print("\n" + "="*70)
    print("✅ 分析完成！")
    print("="*70)
    
    # 输出文件清单
    print("\n📁 生成的文件:")
    print("  1. candidates.json              - 参数簇在文件中的匹配结果")
    print("  2. cluster_pair_tasks.json      - 需要分析的簇对任务列表")
    print("  3. cluster_pair_couplings.json  - LLM分析的簇对耦合结果")
    print("  4. cluster_coupling_matrix.json - 簇对耦合矩阵 (仅包含使用的簇)")
    print("  5. extracted_param_couplings.json - 参数级别的耦合列表")
    print("  6. param_couplings_summary.json - 统计摘要")
    print("  7. coupling_graph.gexf          - 参数耦合关系图\n")


if __name__ == '__main__': 
    main()