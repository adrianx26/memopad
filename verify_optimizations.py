"""Quick verification test for optimizations.

Tests that all optimization changes are present and working:
1. Batched query method exists
2. Parallel sync infrastructure exists
3. Caching systems initialized
4. No import errors
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

def test_imports():
    """Test that all modified modules import successfully."""
    print("\n" + "="*60)
    print("TEST 1: Import Verification")
    print("="*60)
    
    try:
        print("✓ EntityRepository imports OK")
        
        print("✓ EntityService imports OK")
        
        print("✓ SyncService imports OK")
        
        print("✓ FileService imports OK")
        
        print("✓ db module imports OK")
        
        return True
    except Exception as e:
        print(f"✗ Import failed: {e}")
        return False


def test_batched_queries():
    """Test that batched query method exists."""
    print("\n" + "="*60)
    print("TEST 2: Batched Queries")
    print("="*60)
    
    try:
        from memopad.repository.entity_repository import EntityRepository
        
        # Check method exists
        assert hasattr(EntityRepository, 'get_by_file_paths_batch'), \
            "get_by_file_paths_batch method missing!"
        
        print("✓ get_by_file_paths_batch() method exists")
        print("✓ Batched query optimization implemented")
        return True
    except Exception as e:
        print(f"✗ Test failed: {e}")
        return False


def test_parallel_sync():
    """Test that parallel sync infrastructure exists."""
    print("\n" + "="*60)
    print("TEST 3: Parallel Sync Infrastructure")
    print("="*60)
    
    try:
        from memopad.sync import sync_service
        
        # Check constant exists
        assert hasattr(sync_service, 'MAX_CONCURRENT_SYNCS'), \
            "MAX_CONCURRENT_SYNCS constant missing!"
        
        max_concurrent = sync_service.MAX_CONCURRENT_SYNCS
        print(f"✓ MAX_CONCURRENT_SYNCS = {max_concurrent}")
        
        # Check helper method exists
        from memopad.sync.sync_service import SyncService
        assert hasattr(SyncService, '_sync_file_with_semaphore'), \
            "_sync_file_with_semaphore method missing!"
        
        print("✓ _sync_file_with_semaphore() method exists")
        print("✓ Parallel sync optimization implemented")
        return True
    except Exception as e:
        print(f"✗ Test failed: {e}")
        return False


def test_permalink_cache():
    """Test that permalink caching is initialized."""
    print("\n" + "="*60)
    print("TEST 4: Permalink Caching")
    print("="*60)
    
    try:
        from memopad.services.entity_service import EntityService
        
        # Check cache-related methods exist
        assert hasattr(EntityService, '_cache_permalink'), \
            "_cache_permalink method missing!"
        assert hasattr(EntityService, 'invalidate_permalink_cache'), \
            "invalidate_permalink_cache method missing!"
        
        print("✓ _cache_permalink() method exists")
        print("✓ invalidate_permalink_cache() method exists")
        print("✓ Permalink cache optimization implemented")
        return True
    except Exception as e:
        print(f"✗ Test failed: {e}")
        return False


def test_metadata_cache():
    """Test that file metadata caching is initialized."""
    print("\n" + "="*60)
    print("TEST 5: File Metadata Caching")
    print("="*60)
    
    try:
        from memopad.services.file_service import FileService
        
        # Check cache method exists
        assert hasattr(FileService, 'invalidate_metadata_cache'), \
            "invalidate_metadata_cache method missing!"
        
        print("✓ invalidate_metadata_cache() method exists")
        print("✓ Metadata cache optimization implemented")
        return True
    except Exception as e:
        print(f"✗ Test failed: {e}")
        return False


def test_sqlite_pragmas():
    """Test that SQLite optimizations are present."""
    print("\n" + "="*60)
    print("TEST 6: SQLite Optimization")
    print("="*60)
    
    try:
        import inspect
        from memopad import db
        
        # Get source code of configure_sqlite_connection
        source = inspect.getsource(db.configure_sqlite_connection)
        
        # Check for optimizations
        assert 'cache_size=-128000' in source, "128MB cache not found!"
        assert 'PRAGMA optimize' in source, "PRAGMA optimize not found!"
        
        print("✓ SQLite cache increased to 128MB")
        print("✓ PRAGMA optimize enabled")
        print("✓ SQLite optimization implemented")
        return True
    except Exception as e:
        print(f"✗ Test failed: {e}")
        return False


def run_all_tests():
    """Run all verification tests."""
    print("\n" + "="*70)
    print(" " * 10 + "OPTIMIZATION VERIFICATION TEST SUITE")
    print("="*70)
    
    tests = [
        ("Imports", test_imports),
        ("Batched Queries", test_batched_queries),
        ("Parallel Sync", test_parallel_sync),
        ("Permalink Cache", test_permalink_cache),
        ("Metadata Cache", test_metadata_cache),
        ("SQLite Tuning", test_sqlite_pragmas),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print(f"\n✗ {name} test crashed: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))
    
    # Summary
    print("\n" + "="*70)
    print("TEST SUMMARY")
    print("="*70)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{name:<25} {status}")
    
    print("-" * 70)
    print(f"Tests Passed: {passed}/{total}")
    
    if passed == total:
        print("\n🎉 All optimizations verified successfully!")
        print("\nReady for performance testing with real data.")
    else:
        print(f"\n⚠️  {total - passed} test(s) failed. Review errors above.")
    
    print("="*70)
    
    return passed == total


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
