

import asyncio
import time
import statistics
from typing import Dict, List, Tuple
from sqlalchemy.orm import joinedload, selectinload
from sqlalchemy import select

class PermissionPerformanceTester:
    def __init__(self, db):
        self.db = db
        self.dao = ResourceInstanceDao(self.db)
    
    async def _get_instance_eager_load(self, instance_uuid: str) -> ResourceInstance:
        """预加载方案（原方案）- 单次查询JOIN所有表"""
        stmt = (
            select(ResourceInstance)
            .where(ResourceInstance.uuid == instance_uuid)
            .options(
                joinedload(ResourceInstance.resource)
                .joinedload(Resource.project)
                .joinedload(Project.workspace)
                .options(
                    joinedload(Workspace.user_owner),
                    joinedload(Workspace.team)
                )
            )
        )
        result = await self.db.execute(stmt)
        instance = result.scalars().first()
        if instance and instance.resource and instance.resource.project:
            workspace = instance.resource.project.workspace
            _ = workspace.user_owner
            _ = workspace.team
            print(f"预加载 - Workspace ID: {workspace.id if workspace else 'None'}")
        return instance

    async def _get_instance_base_dao(self, instance_uuid: str) -> ResourceInstance:
        withs = [{
            "name": "resource",
            "withs": [{
                "name": "project", 
                "withs": [{
                    "name": "workspace",
                    "withs": [
                        {"name": "user_owner"},
                        {"name": "team"}
                    ]
                }]
            }]
        }]
        instance = await self.dao.get_one(
            where={"uuid": instance_uuid},
            withs=withs
        )
        if instance and instance.resource and instance.resource.project:
            workspace = instance.resource.project.workspace
            _ = workspace.user_owner
            _ = workspace.team
            print(f"BaseDao - Workspace ID: {workspace.id if workspace else 'None'}")
        return instance

    async def _get_instance_lazy_load(self, instance_uuid: str) -> ResourceInstance:
        """懒加载方案 - 使用ORM关系按需加载"""
        stmt = select(ResourceInstance).where(ResourceInstance.uuid == instance_uuid)
        result = await self.db.execute(stmt)
        instance = result.scalars().first()
        
        if not instance:
            return None
        
        # 按需加载关系
        await self.db.refresh(instance, ['resource'])
        
        if instance.resource:
            await self.db.refresh(instance.resource, ['project'])
        
        if instance.resource and instance.resource.project:
            await self.db.refresh(instance.resource.project, ['workspace'])
        
        if instance.resource and instance.resource.project and instance.resource.project.workspace:
            workspace = instance.resource.project.workspace
            await self.db.refresh(workspace, ['user_owner', 'team'])
            print(f"懒加载 - Workspace ID: {workspace.id if workspace else 'None'}")
        
        return instance

    async def _get_instance_refetch(self, instance_uuid: str) -> ResourceInstance:
        """re-fetch方案 - 通过多个独立查询获取数据"""
        # 1. 查询ResourceInstance
        stmt = select(ResourceInstance).where(ResourceInstance.uuid == instance_uuid)
        result = await self.db.execute(stmt)
        instance = result.scalars().first()
        
        if not instance:
            return None
        
        # 2. 查询Resource
        if instance.resource_id:
            resource_stmt = select(Resource).where(Resource.id == instance.resource_id)
            resource_result = await self.db.execute(resource_stmt)
            instance.resource = resource_result.scalars().first()
        
        # 3. 查询Project
        if instance.resource and instance.resource.project_id:
            project_stmt = select(Project).where(Project.id == instance.resource.project_id)
            project_result = await self.db.execute(project_stmt)
            instance.resource.project = project_result.scalars().first()
        
        # 4. 查询Workspace
        if instance.resource and instance.resource.project and instance.resource.project.workspace_id:
            workspace_stmt = select(Workspace).where(Workspace.id == instance.resource.project.workspace_id)
            workspace_result = await self.db.execute(workspace_stmt)
            instance.resource.project.workspace = workspace_result.scalars().first()
        
        # 5. 查询User和Team
        if instance.resource and instance.resource.project and instance.resource.project.workspace:
            workspace = instance.resource.project.workspace
            
            # 查询User
            if workspace.owner_user_id:
                user_stmt = select(User).where(User.id == workspace.owner_user_id)
                user_result = await self.db.execute(user_stmt)
                workspace.user_owner = user_result.scalars().first()
            
            # 查询Team
            if workspace.owner_team_id:
                team_stmt = select(Team).where(Team.id == workspace.owner_team_id)
                team_result = await self.db.execute(team_stmt)
                workspace.team = team_result.scalars().first()
            
            print(f"Re-fetch - Workspace ID: {workspace.id if workspace else 'None'}")
        
        return instance
    
    async def _get_instance_selectin_load(self, instance_uuid: str) -> ResourceInstance:
        """selectin加载方案 - 另一种预加载优化"""
        stmt = (
            select(ResourceInstance)
            .where(ResourceInstance.uuid == instance_uuid)
            .options(
                selectinload(ResourceInstance.resource)
                .selectinload(Resource.project)
                .selectinload(Project.workspace)
                .options(
                    selectinload(Workspace.user_owner),
                    selectinload(Workspace.team)
                )
            )
        )
        result = await self.db.execute(stmt)
        instance = result.scalars().first()
        if instance and instance.resource and instance.resource.project:
            workspace = instance.resource.project.workspace
            print(f"Selectin加载 - Workspace ID: {workspace.id if workspace else 'None'}")
        return instance

class ScientificBenchmark:
    def __init__(self, db):
        self.db = db
        self.tester = PermissionPerformanceTester(db)
        self.warmup_rounds = 3
        self.test_rounds = 7
        self.cooldown_seconds = 0.1
    
    async def _warmup(self, instance_uuid: str):
        """预热阶段 - 消除冷启动影响"""
        print("🔥 开始预热阶段...")
        methods = [
            self.tester._get_instance_eager_load,
            self.tester._get_instance_base_dao,
            self.tester._get_instance_lazy_load,
            self.tester._get_instance_refetch,
            self.tester._get_instance_selectin_load
        ]
        
        for round_num in range(self.warmup_rounds):
            for method in methods:
                await method(instance_uuid)
                await asyncio.sleep(self.cooldown_seconds)
            print(f"预热轮次 {round_num + 1}/{self.warmup_rounds} 完成")
    
    async def _run_benchmark_round(self, instance_uuid: str, round_num: int):
        """运行一轮基准测试"""
        results = {}
        
        # 定义所有测试方法
        methods = [
            ('预加载(joinedload)', self.tester._get_instance_eager_load),
            ('Basedao', self.tester._get_instance_base_dao),
            ('懒加载', self.tester._get_instance_lazy_load),
            ('Re-fetch', self.tester._get_instance_refetch),
            ('预加载(selectinload)', self.tester._get_instance_selectin_load)
        ]
        
        # 根据轮次号决定顺序（轮换以避免顺序偏差）
        methods = self._rotate_methods(methods, round_num)
        
        for method_name, method in methods:
            # 清除可能的缓存
            await self._clear_orm_cache()
            
            # 执行测试
            start_time = time.perf_counter()
            result = await method(instance_uuid)
            end_time = time.perf_counter()
            
            execution_time = (end_time - start_time) * 1000
            results[method_name] = execution_time
            
            await asyncio.sleep(self.cooldown_seconds)
        
        return results
    
    def _rotate_methods(self, methods, round_num):
        """轮换方法顺序以避免测试偏差"""
        index = round_num % len(methods)
        return methods[index:] + methods[:index]
    
    async def _clear_orm_cache(self):
        """清除ORM缓存以获得更准确的结果"""
        # 如果使用SQLAlchemy，可以尝试清除会话缓存
        try:
            if hasattr(self.db, 'expire_all'):
                self.db.expire_all()
        except:
            pass  # 忽略缓存清除错误
    
    async def run_benchmark(self, instance_uuid: str):
        """运行完整的科学基准测试"""
        print("🔬 开始科学基准测试...")
        
        # 预热
        await self._warmup(instance_uuid)
        
        # 基准测试
        print(f"📈 开始基准测试，共 {self.test_rounds} 轮...")
        all_results = []
        
        for round_num in range(self.test_rounds):
            round_results = await self._run_benchmark_round(instance_uuid, round_num)
            all_results.append(round_results)
            
            print(f"轮次 {round_num + 1}: ", end="")
            for method, time_ms in round_results.items():
                print(f"{method}={time_ms:.2f}ms ", end="")
            print()
        
        # 统计分析
        self._analyze_results(all_results)
        
        return all_results
    
    def _analyze_results(self, all_results):
        """分析测试结果"""
        # 提取每种方法的所有测试时间
        method_times = {}
        for method_name in all_results[0].keys():
            times = [r[method_name] for r in all_results]
            method_times[method_name] = times
        
        # 对每种方法进行统计分析
        stats = {}
        for method_name, times in method_times.items():
            # 移除可能的异常值（使用IQR方法）
            clean_times = self._remove_outliers(times)
            
            stats[method_name] = {
                '原始数据': times,
                '清洁数据': clean_times,
                '平均值': statistics.mean(clean_times),
                '中位数': statistics.median(clean_times),
                '标准差': statistics.stdev(clean_times) if len(clean_times) > 1 else 0,
                '最小值': min(clean_times),
                '最大值': max(clean_times)
            }
        
        # 输出结果
        self._print_detailed_analysis(stats)
        
        return stats
    
    def _remove_outliers(self, data):
        """使用IQR方法移除异常值"""
        if len(data) < 3:
            return data
        
        try:
            Q1 = statistics.quantiles(data, n=4)[0]
            Q3 = statistics.quantiles(data, n=4)[2]
            IQR = Q3 - Q1
            lower_bound = Q1 - 1.5 * IQR
            upper_bound = Q3 + 1.5 * IQR
            return [x for x in data if lower_bound <= x <= upper_bound]
        except:
            return data  # 如果计算失败，返回原始数据
    
    def _print_detailed_analysis(self, stats):
        """打印详细分析结果"""
        print("\n" + "="*80)
        print("🔍 详细性能分析报告")
        print("="*80)
        
        # 按平均耗时排序
        sorted_methods = sorted(stats.items(), key=lambda x: x[1]['平均值'])
        
        for i, (method_name, method_stats) in enumerate(sorted_methods, 1):
            print(f"\n#{i} {method_name}:")
            print(f"   样本数量: {len(method_stats['清洁数据'])}")
            print(f"   平均耗时: {method_stats['平均值']:.2f} ms")
            print(f"   中位数: {method_stats['中位数']:.2f} ms")
            print(f"   标准差: {method_stats['标准差']:.2f} ms (稳定性)")
            print(f"   耗时范围: {method_stats['最小值']:.2f}-{method_stats['最大值']:.2f} ms")
        
        # 性能对比
        fastest = sorted_methods[0]
        slowest = sorted_methods[-1]
        
        improvement = ((slowest[1]['平均值'] - fastest[1]['平均值']) / 
                      slowest[1]['平均值'] * 100)
        
        print(f"\n🏆 性能冠军: {fastest[0]}")
        print(f"   {fastest[0]} 比 {slowest[0]} 快 {improvement:.1f}%")
        
        # 稳定性对比
        most_stable = min(stats.items(), 
                         key=lambda x: x[1]['标准差'] if x[1]['标准差'] > 0 else float('inf'))
        least_stable = max(stats.items(), 
                          key=lambda x: x[1]['标准差'] if len(x[1]['清洁数据']) > 1 else 0)
        
        if most_stable[0] != least_stable[0]:
            print(f"📈 {most_stable[0]} 的性能最稳定 (标准差: {most_stable[1]['标准差']:.2f} ms)")
            print(f"📉 {least_stable[0]} 的性能波动最大 (标准差: {least_stable[1]['标准差']:.2f} ms)")
        
        # 各方案特点分析
        print(f"\n💡 各方案特点分析:")
        print(f"  • 预加载(joinedload): 单次复杂查询，适合关系复杂但数据量不大的情况")
        print(f"  • 预加载(selectinload): 多次简单查询，避免JOIN的笛卡尔积问题")
        print(f"  • 懒加载: 按需加载，首次访问关系时会产生额外查询")
        print(f"  • Re-fetch: 完全控制查询过程，避免ORM的魔法，但代码量最多")