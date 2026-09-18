# NotificationsController: notificationTester.ps1
# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.
#
# A small window for sending test notifications, so NotificationsController can be tried without
# waiting for real apps. Run it with Windows PowerShell 5.1, which has the WinForms and WinRT support
# it needs:
#
#     powershell -ExecutionPolicy Bypass -File tools\notificationTester.ps1
#
# It sends two kinds of notification:
# 1. A UI Automation notification event, raised by the window itself (the app module is powershell).
#    Use the delay to switch to another app first, to see how background notifications are handled.
# 2. A Windows toast, sent as Windows PowerShell.

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

function Send-UiaNotification([System.Windows.Forms.Control]$control, [string]$text, [string]$processing) {
	$kind = [System.Windows.Forms.Automation.AutomationNotificationKind]::Other
	$proc = [System.Windows.Forms.Automation.AutomationNotificationProcessing]::$processing
	[void]$control.AccessibilityObject.RaiseAutomationNotification($kind, $proc, $text)
}

function Send-Toast([string]$title, [string]$text) {
	[void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
	[void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]
	$escape = { param($s) [System.Security.SecurityElement]::Escape($s) }
	$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
	$xml.LoadXml(
		"<toast><visual><binding template=`"ToastGeneric`"><text>$(& $escape $title)</text>" +
		"<text>$(& $escape $text)</text></binding></visual></toast>"
	)
	$appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
	$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
	[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
}

$form = New-Object System.Windows.Forms.Form
$form.Text = 'Notification tester'
$form.AutoSize = $true
$form.AutoSizeMode = 'GrowAndShrink'
$form.Padding = New-Object System.Windows.Forms.Padding(10)

$layout = New-Object System.Windows.Forms.FlowLayoutPanel
$layout.FlowDirection = 'TopDown'
$layout.AutoSize = $true
$form.Controls.Add($layout)

$textLabel = New-Object System.Windows.Forms.Label
$textLabel.Text = '&Text:'
$textLabel.AutoSize = $true
$layout.Controls.Add($textLabel)
$textBox = New-Object System.Windows.Forms.TextBox
$textBox.Width = 350
$textBox.Text = 'Test notification'
$textBox.AccessibleName = 'Text'
$layout.Controls.Add($textBox)

$delayLabel = New-Object System.Windows.Forms.Label
$delayLabel.Text = '&Delay in seconds (switch to another app to test background notifications):'
$delayLabel.AutoSize = $true
$layout.Controls.Add($delayLabel)
$delayBox = New-Object System.Windows.Forms.NumericUpDown
$delayBox.Minimum = 0
$delayBox.Maximum = 60
$delayBox.Value = 0
$delayBox.AccessibleName = 'Delay in seconds'
$layout.Controls.Add($delayBox)

$processingLabel = New-Object System.Windows.Forms.Label
$processingLabel.Text = '&Processing:'
$processingLabel.AutoSize = $true
$layout.Controls.Add($processingLabel)
$processingBox = New-Object System.Windows.Forms.ComboBox
$processingBox.DropDownStyle = 'DropDownList'
[void]$processingBox.Items.AddRange(@('ImportantAll', 'ImportantMostRecent', 'All', 'MostRecent', 'CurrentThenMostRecent'))
$processingBox.SelectedItem = 'All'
$processingBox.AccessibleName = 'Processing'
$layout.Controls.Add($processingBox)

$counter = 0
$pending = $null

function Next-Text {
	$script:counter++
	return "$($textBox.Text) $script:counter"
}

function Send-Now($request) {
	switch ($request.Kind) {
		'uia' { Send-UiaNotification $form $request.Text $request.Processing }
		'twice' {
			Send-UiaNotification $form $request.Text $request.Processing
			Send-UiaNotification $form $request.Text $request.Processing
		}
		'toast' { Send-Toast 'Notification tester' $request.Text }
	}
}

$timer = New-Object System.Windows.Forms.Timer
$timer.Add_Tick({
	$timer.Stop()
	Send-Now $script:pending
})

function Send-AfterDelay([string]$kind) {
	$request = @{ Kind = $kind; Text = (Next-Text); Processing = [string]$processingBox.SelectedItem }
	$seconds = [int]$delayBox.Value
	if ($seconds -le 0) {
		Send-Now $request
		return
	}
	$script:pending = $request
	$timer.Interval = $seconds * 1000
	$timer.Start()
}

$uiaButton = New-Object System.Windows.Forms.Button
$uiaButton.Text = 'Send &UIA notification'
$uiaButton.AutoSize = $true
$uiaButton.Add_Click({ Send-AfterDelay 'uia' })
$layout.Controls.Add($uiaButton)

$twiceButton = New-Object System.Windows.Forms.Button
$twiceButton.Text = 'Send the same UIA notification t&wice'
$twiceButton.AutoSize = $true
$twiceButton.Add_Click({ Send-AfterDelay 'twice' })
$layout.Controls.Add($twiceButton)

$toastButton = New-Object System.Windows.Forms.Button
$toastButton.Text = 'Send &toast'
$toastButton.AutoSize = $true
$toastButton.Add_Click({ Send-AfterDelay 'toast' })
$layout.Controls.Add($toastButton)

$closeButton = New-Object System.Windows.Forms.Button
$closeButton.Text = 'Close'
$closeButton.AutoSize = $true
$closeButton.Add_Click({ $form.Close() })
$layout.Controls.Add($closeButton)
$form.CancelButton = $closeButton

[void]$form.ShowDialog()
