//! Windows job object that ties the sidecar's lifetime to the shell's.
//!
//! A uvicorn child that outlives a crashed or closed shell would keep port
//! and file locks on the user's machine (C9 §11 "孤儿 uvicorn" risk). Windows
//! job objects with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` make the kernel do
//! the reaping: every descendant process dies when the shell's job handle
//! goes away, no matter how the shell exited.

#![cfg(windows)]

use std::os::windows::io::AsRawHandle;
use std::process::Child;
use std::ptr;

use windows_sys::Win32::Foundation::{CloseHandle, HANDLE};
use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
    SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};

/// Owns the shell's job object. Dropping it terminates every assigned
/// descendant process.
pub struct JobGuard {
    handle: HANDLE,
}

// The raw handle is only used from whichever thread owns the guard; the job
// object itself is kernel-synchronized.
unsafe impl Send for JobGuard {}

impl JobGuard {
    /// Create the kill-on-close job and assign the child to it.
    pub fn for_child(child: &Child) -> Option<Self> {
        unsafe {
            let handle = CreateJobObjectW(ptr::null(), ptr::null());
            if handle.is_null() {
                return None;
            }
            let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            let ok = SetInformationJobObject(
                handle,
                JobObjectExtendedLimitInformation,
                &limits as *const _ as *const core::ffi::c_void,
                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            );
            if ok == 0 {
                CloseHandle(handle);
                return None;
            }
            // Guard against accidentally killing the shell itself when a
            // nested process is spawned from the shell's own job.
            if AssignProcessToJobObject(handle, child.as_raw_handle() as HANDLE) == 0 {
                CloseHandle(handle);
                return None;
            }
            Some(Self { handle })
        }
    }
}

impl Drop for JobGuard {
    fn drop(&mut self) {
        unsafe {
            CloseHandle(self.handle);
        }
    }
}
