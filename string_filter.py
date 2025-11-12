"""
智能字符串匹配预筛选
作者: @Fuhj-better
日期: 2025-11-11
"""

import os
import re
import json
from pathlib import Path
from typing import Dict, List, Tuple
from dataclasses import dataclass
from concurrent.futures import ProcessPoolExecutor


@dataclass
class MatchResult:
    """匹配结果数据类"""
    file_path: str
    matched_params: List[str]
    match_count: int
    code_contexts: List[Dict]
    file_type: str  # def/dut/tb
    

class SmartStringMatcher:
    """智能字符串匹配器（避免常见误报）"""
    
    def __init__(self, driver_root: str):
        self.driver_root = Path(driver_root)
        
    def remove_noise(self, content: str) -> str:
        """移除注释和字符串字面量（减少误匹配）"""
        # 移除单行注释 //
        content = re.sub(r'//.*?$', '', content, flags=re.MULTILINE)
        # 移除多行注释 /* */
        content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
        # 移除字符串 "..."
        content = re.sub(r'"[^"]*"', '""', content)
        return content
    
    def extract_context(self, content: str, match_pos: int, 
                       window: int = 200) -> Dict:
        """提取匹配位置的代码上下文"""
        lines = content[:match_pos].count('\n')
        start = max(0, match_pos - window)
        end = min(len(content), match_pos + window)
        
        return {
            'line_number': lines + 1,
            'snippet': content[start:end],
            'position': match_pos
        }
    
    def match_params_in_file(self, file_path: Path, 
                            params: List[str]) -> MatchResult:
        """在单个文件中匹配参数"""
        
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                raw_content = f.read()
        except Exception as e:
            print(f"⚠️  无法读取文件 {file_path}: {e}")
            return None
        
        # 清理内容
        cleaned_content = self.remove_noise(raw_content)
        
        matched_params = []
        contexts = []
        total_matches = 0
        
        for param in params:
            # 使用词边界正则（避免部分匹配，如 CLK_EN 匹配到 CLK_ENABLE）
            pattern = r'\b' + re.escape(param) + r'\b'
            matches = list(re.finditer(pattern, cleaned_content))
            
            if matches:
                matched_params.append(param)
                total_matches += len(matches)
                
                # 保存前3个匹配位置的上下文
                for match in matches[:3]:
                    ctx = self.extract_context(cleaned_content, match.start())
                    ctx['param'] = param
                    contexts.append(ctx)
        
        # 判断文件类型
        file_type = self.classify_file(file_path)
        
        return MatchResult(
            file_path=str(file_path),
            matched_params=matched_params,
            match_count=total_matches,
            code_contexts=contexts,
            file_type=file_type
        )
    
    def classify_file(self, file_path: Path) -> str:
        """分类文件类型"""
        path_str = str(file_path)
        if '/def/' in path_str or '\\def\\' in path_str:
            return 'def'
        elif '/dut/' in path_str or '\\dut\\' in path_str:
            return 'dut'
        elif '/tb/' in path_str or '\\tb\\' in path_str:
            return 'tb'
        return 'unknown'
    
    def scan_cluster(self, cluster_name: str, 
                    params: List[str]) -> List[MatchResult]:
        """扫描一个参数簇"""
        
        results = []
        
        # 遍历driver下所有.sv/.v文件
        for sv_file in self.driver_root.rglob('*.sv'):
            result = self.match_params_in_file(sv_file, params)
            if result and result.match_count > 0:
                results.append(result)
        
        for v_file in self.driver_root.rglob('*.v'):
            result = self.match_params_in_file(v_file, params)
            if result and result.match_count > 0:
                results.append(result)
        
        # 按匹配数量排序
        results.sort(key=lambda x: x.match_count, reverse=True)
        
        return results
    
    def parallel_scan(self, clusters: Dict[str, List[str]], 
                     max_workers: int = 4) -> Dict:
        """并行扫描所有簇"""
        
        all_results = {}
        
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self.scan_cluster, name, params): name
                for name, params in clusters.items()
            }
            
            for future in futures:
                cluster_name = futures[future]
                try:
                    results = future.result()
                    all_results[cluster_name] = [
                        {
                            'file': r.file_path,
                            'matched_params': r.matched_params,
                            'match_count': r.match_count,
                            'file_type': r.file_type,
                            'contexts': r.code_contexts[:3]  # 最多保留3个上下文
                        }
                        for r in results
                    ]
                    print(f"✅ {cluster_name}: 找到 {len(results)} 个相关文件")
                except Exception as e:
                    print(f"❌ {cluster_name} 扫描失败: {e}")
        
        return all_results


def main():
    """主函数"""
    
    # 1. 加载参数簇
    with open('clusters.json', 'r') as f:
        clusters = json.load(f)
    
    print(f"📦 加载了 {len(clusters)} 个参数簇")
    
    # 2. 执行字符串匹配
    matcher = SmartStringMatcher(driver_root='driver/')
    
    print("\n🔍 开始字符串匹配扫描...\n")
    candidates = matcher.parallel_scan(clusters, max_workers=8)
    
    # 3. 保存结果
    with open('candidates.json', 'w') as f:
        json.dump(candidates, f, indent=2, ensure_ascii=False)
    
    # 4. 统计输出
    total_candidates = sum(len(files) for files in candidates.values())
    print(f"\n📊 扫描完成！")
    print(f"   - 总候选文件对: {total_candidates}")
    print(f"   - 已保存到: candidates.json")
    
    # 5. 显示每个簇的top文件
    print("\n📋 每个簇的高相关文件：")
    for cluster, files in candidates.items():
        if files:
            top_file = files[0]
            print(f"   {cluster}: {Path(top_file['file']).name} "
                  f"({top_file['match_count']}次匹配)")


if __name__ == '__main__':
    main()