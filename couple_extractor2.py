from concurrent.futures import ProcessPoolExecutor
import json
import os
import re
from pathlib import Path
from typing import List, Dict, Any, Set, Tuple
from collections import defaultdict
from dataclasses import dataclass, asdict
import networkx as nx

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

class StringMatcher:
    """字符串匹配：按簇扫描文件中的参数"""
    
    def __init__(self, driver_root: Path, clusters: Dict, params_info: Dict[str, ParamInfo]):
        self.driver_root = driver_root
        self.clusters = clusters
        self.params_info = params_info

    def remove_noise(self, content: str) -> str:
        """移除注释和字符串字面量（减少误匹配）"""
        # # 移除单行注释 //
        # content = re.sub(r'//.*?$', '', content, flags=re.MULTILINE)
        # # 移除多行注释 /* */
        # content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
        # # 移除字符串 "..."
        # content = re.sub(r'"[^"]*"', '""', content)
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



